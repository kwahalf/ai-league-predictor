# Adaptable Agentic Feature

## Overview

A domain-adaptable agent runs on the template's existing stack: the agent loop lives in a Temporal workflow, tool implementations live in a Python registry, and a per-agent YAML file declares which tools an agent may call. Tools can require human approval, which pauses the workflow on a Temporal signal until someone approves or rejects from the browser. Adapting the agent to a new domain means writing tool functions and editing YAML — never editing the loop.

## Metadata

- **Feature Name**: Adaptable Agentic Feature
- **Status**: Implemented (2026-09-08). See docs/user-guide/agent.md.
- **Priority**: P1 - High
- **Owner**: Jeevan Dulai
- **Created**: 2026-09-08
- **Last Updated**: 2026-09-08
- **Reference implementation**: `zonal-ops-agent` (`temporal/src/activities/llm_agent.ts`, `temporal/src/workflows/pack_tools.ts`, `temporal/packs/schemas/tools.schema.json`)

## Problem Statement

### The problem

The template ships a durable-workflow stack, a generalisable entity/fact schema and a JSON-driven UI, but nothing agentic. Anyone adopting it to build an agent has to invent the loop, the tool contract, the human-approval mechanism and the persistence model, and the obvious first attempt — an LLM loop inside a request handler — throws away the durability the stack exists to provide.

### Current state

`zonal-ops-agent` solves this in TypeScript for one product, at production scale: a provider-agnostic `llm_agent` activity, domain packs that declare tool allowlists, and a hard rule that a pack may narrow the framework's tools but never widen them. That design is sound and worth reproducing, but it is large, tangled with that product's analytics domain, and in the wrong language for this template (this worker is Python).

### Desired state

Repo users get a working agent skeleton and add their domain to it. The loop, the provider seam, persistence, approval and the UI are framework concerns and are supplied. Tools, prompts and the output contract are domain concerns and are authored per project.

## Goals and Non-Goals

### Goals

- One generic agent loop that no domain ever edits.
- Adding a tool is one Python function plus one line of YAML — no workflow change, no worker registration change, no frontend change.
- A privilege boundary that holds by construction: a domain config can only narrow the framework's tool set.
- Human-in-the-loop approval for write tools, durable across worker restarts.
- Provider-agnostic model access: Claude via the Anthropic API, Bedrock or Microsoft Foundry, plus Azure OpenAI (GPT) behind the same internal interface.
- The whole loop is runnable with no model credentials at all, so nobody is blocked on key provisioning.

### Non-Goals

- Token streaming to the browser. Runs are polled; the transcript is the artifact.
- Scheduled or data-triggered runs ("the agent watches your data"). Noted as a later extension.
- Multi-agent delegation, sub-agents or agent-to-agent messaging.
- Retrieval/embeddings/vector search.
- Authentication or per-user data scoping. The template has no auth; this feature does not add one.

### Success criteria

- A repo user adds a read tool and sees the agent call it, without touching framework files.
- A write tool marked for approval pauses a run; killing and restarting the worker container does not lose the pending run; approving resumes it.
- The same agent YAML runs against Claude and against Azure OpenAI by changing one field.
- The full test suite passes with no network access and no API key.

## Decisions

| Decision | Rationale |
|---|---|
| Skeleton, not a finished reference agent | Repo users learn tool design by writing tools. The framework supplies two reference tools as worked examples — one read, one write — and the write tool exists so the approval path is demonstrable out of the box. |
| Agent loop in the Temporal workflow; LLM call and each tool call are activities | Per-tool retry without re-paying for model calls, per-tool idempotency for write tools, human approval as a plain signal, and every step visible in the Temporal UI. |
| All tools dispatched through one generic `run_tool` activity | Adding a tool touches one file. Named-activity-per-tool would mean editing worker registration for every new tool, which defeats the adaptation goal. |
| Python tool registry + per-agent YAML | The framework owns implementations; YAML only chooses among them. Mirrors zonal's pack allowlists without a pack subsystem. |
| Manual tool loop, not the SDK's tool runner | The loop must survive across activity boundaries and worker restarts, which a client-side runner cannot do. Also avoids a beta dependency. |
| `output_config.format` for the final answer, not a forced `submit_response` tool | Current Claude API supports a JSON-Schema output format alongside tools, so the schema contract needs no synthetic tool. (zonal predates this and uses the tool trick.) |
| A `fake` provider ships as a first-class adapter | Makes the entire feature demonstrable and testable with no credentials, and makes the test suite hermetic. |

## Technical Design

### Architecture

```
browser ──POST /agent/runs──▶ agent-api (FastAPI, worker image, port 8000)
                                    │ start_workflow
                                    ▼
                              AgentWorkflow  ◀── signal: submit_tool_decision
                                │  per round:
                                │    llm_decide      activity → llm/ seam → provider
                                │    allowlist check  ← agent YAML
                                │    run_tool         activity → tool registry
                                │    persist_step     activity → Supabase
                                ▼
browser ◀── polls agent_runs / agent_messages through the existing Supabase data source
```

The workflow is the only component that decides what may run. The registry is the only component that executes. The YAML is the only place a domain speaks. No component performs two of those jobs.

### New files

```
supabase/migrations/<ts>_agent_runs.sql
temporal/agents/example_analyst.yaml          # reference agent config
temporal/src/agent/config.py                  # YAML load + validation
temporal/src/agent/registry.py                # @tool decorator, list_tools, dispatch
temporal/src/agent/tools.py                   # domain tools — where repo users work
temporal/src/agent/llm/types.py               # neutral AssistantTurn / ToolCall
temporal/src/agent/llm/claude.py              # anthropic | bedrock | foundry
temporal/src/agent/llm/azure_gpt.py           # Azure OpenAI chat-completions shape
temporal/src/agent/llm/fake.py                # domain-agnostic stand-in, no credentials
temporal/src/agent/contracts.py               # dataclasses crossing the workflow boundary
temporal/src/activities/agent.py              # llm_decide, run_tool, persist_step, ...
temporal/src/workflows/agent/agent_workflow.py
temporal/src/api.py                           # FastAPI trigger + decision endpoints
temporal/tests/…                              # registry, config, adapters, tools, api, loop
frontend/src/pages/agent.json                 # JSON-driven agent page
frontend/src/routes/agent.tsx                 # route: custom handlers + selected run
frontend/src/data/agentApi.ts                 # the two calls Supabase cannot make
docs/user-guide/agent.md                      # how to adapt it
```

Modified: `temporal/src/worker.py` (register the workflow and activities), `temporal/pyproject.toml` (add `anthropic`, `pyyaml`, `fastapi`, `uvicorn`, `pytest-asyncio`), `temporal/Dockerfile` (dev extras so tests run in the image; copy `agents/`), `docker-compose.yml` (the `agent-api` service and provider env), `.env` / `.env.example` (provider settings), `frontend/src/engine/UIEngine.tsx` (wire `customHandlers`), `frontend/src/engine/useDataSources.ts` and `types.ts` (`refetchInterval` / `refetchWhile`), `frontend/src/routes/__root.tsx` (nav link).

### Database Schema

```sql
create table agent_runs (
  id uuid primary key default gen_random_uuid(),
  agent text not null,
  question text not null,
  status text not null default 'running'
    check (status in ('running', 'awaiting_approval', 'completed', 'failed')),
  answer jsonb,
  error text,
  workflow_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table agent_messages (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  seq integer not null,
  role text not null check (role in ('user', 'assistant', 'tool')),
  content jsonb not null,
  tool_name text,
  tokens_in integer,
  tokens_out integer,
  created_at timestamptz not null default now(),
  unique (run_id, seq)
);

create table agent_approvals (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  tool_call_id text not null,
  tool_name text not null,
  arguments jsonb not null,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  decided_by text,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  unique (run_id, tool_call_id)
);
```

Two properties are load-bearing rather than incidental:

- **`unique (run_id, seq)` plus `on conflict do nothing`** makes `persist_step` idempotent. A retried activity cannot double-write a transcript entry.
- **Read/write asymmetry.** `select` is granted to `anon` and `authenticated`; `insert`/`update`/`delete` are `service_role` only. The browser reads the transcript; only the worker writes it. This is the same posture as `notifications` in `20260908090000_entity_operations.sql`.

### Activities and who owns each write

| Activity | Responsibility | Writes |
|---|---|---|
| `load_agent_config` | Read and validate the agent YAML, resolve the allowlist | — |
| `llm_decide` | One model call through the provider seam; returns an `AssistantTurn` | — |
| `run_tool` | Validate arguments, dispatch through the registry, truncate the result | domain tables, per tool |
| `persist_step` | Append one transcript entry | `agent_messages` |
| `record_pending_approval` | Insert the pending approval and set the run to `awaiting_approval` | `agent_approvals`, `agent_runs.status` |
| `record_decision` | Record an approve/reject outcome and return the run to `running` | `agent_approvals`, `agent_runs.status` |
| `finalise_run` | Write the final answer or the failure, and the terminal status | `agent_runs` |

The workflow's in-memory `_status` exists only to answer the `get_status` query; every status a browser can see is written by an activity, so a worker restart cannot leave the database disagreeing with the workflow. The API endpoint only signals — it never writes `agent_approvals` — so the decision is recorded exactly once, by the workflow, after the signal is durably in history.

### Agent configuration

The configuration below is the full shape. The agent that ships names only the one reference tool (`list_entity_types`); `search_entities` and `create_entity` appear here to show the fields, and are among the tools repo users write.

```yaml
# temporal/agents/example_analyst.yaml
name: example_analyst
provider: claude              # claude | bedrock | foundry | azure_gpt | fake
model: claude-opus-5
max_tool_rounds: 6
system_prompt: |
  You answer questions about the entities in this system using only the
  tools provided. Never assert a fact you did not retrieve with a tool.
answer_schema:                # rendered as output_config.format
  type: object
  properties:
    summary:  { type: string }
    findings: { type: array, items: { type: string } }
  required: [summary, findings]
  additionalProperties: false
tools:
  - name: list_entity_types
  - name: search_entities
  - name: create_entity
    requires_approval: true
    timeout_seconds: 30
```

Rules enforced by `config.py` at load time, each failing the run at its first step and naming the offending tool:

1. **Narrow only.** Every `tools[].name` must exist in the registry. A config cannot define a tool, describe its behaviour, or name the activity behind it. This is zonal's rule (`pack_tools.ts`: "a pack may narrow the framework's tools and may not add to them"), reduced to its essentials.
2. **No escalation by omission.** A tool the registry marks `writes=True` must carry `requires_approval: true`, unless the config sets `allow_unapproved_writes: true` explicitly. Forgetting the flag cannot silently grant an unattended write.
3. **Schema shape.** `answer_schema` must be an object schema with `additionalProperties: false`, which is what the strict output format requires.

### Tool registry

```python
@tool(
    name="search_entities",
    description="Find entities of a type whose current version name matches a query.",
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["entity_type", "query"],
        "additionalProperties": False,
    },
    writes=False,
)
def search_entities(entity_type: str, query: str, limit: int = 10) -> dict:
    ...
```

The decorator registers into a module-level dict serving both callers: `list_tools(names)` renders the model-facing declarations, `dispatch(name, args)` executes. One source of truth, so a tool cannot be advertised and unimplemented, or implemented and unreachable.

`run_tool` truncates each result to a declared maximum before returning it, which keeps the transcript inside Temporal's payload limits and is a constraint worth stating out loud rather than discovering.

### Provider seam

```
llm/types.py     ToolCall(id, name, args)      # id is the provider's own tool-call id
                 AssistantTurn(text?, tool_calls[], raw, usage, final, answer?)
llm/claude.py    Anthropic() | AnthropicBedrockMantle(aws_region=…) | AnthropicFoundry(…)
llm/azure_gpt.py tools[].function / tool_calls / role="tool"
llm/fake.py      replays scripted turns from a fixture
```

Claude on the first-party API, Bedrock and Foundry share one code path — the SDK exposes the same `messages.create` surface from different client classes, so the provider is a constructor choice, not an adapter. Azure OpenAI with GPT models is a genuine adapter because the wire format differs.

`ToolCall.id` is whatever the provider called it (`toolu_…` on Claude, a `tool_calls[].id` on Azure OpenAI). It is the key the transcript, `agent_approvals.tool_call_id`, the decision endpoint and the workflow's decision map all use, so a human decision is unambiguously bound to one tool call rather than to a tool name that may be called twice in a run.

`AssistantTurn.raw` carries the provider's own content blocks back through the workflow untouched. This matters concretely: on Claude, thinking blocks must be echoed back unchanged on the next request, so the loop appends whole content and never cherry-picks the text. The workflow reads only the normalised fields, so nothing above the seam knows which provider ran.

Enabled by default in `claude.py`, both documented so they can be removed: adaptive thinking (on by default for `claude-opus-5`) and server-side refusal fallbacks, so a `stop_reason: "refusal"` degrades to a fallback model instead of a stack trace.

### The workflow

```python
@workflow.defn
class AgentWorkflow:
    def __init__(self) -> None:
        self._decisions: dict[str, ToolDecision] = {}
        self._status = "running"

    @workflow.run
    async def run(self, req: AgentRunRequest) -> dict:
        cfg = await workflow.execute_activity(load_agent_config, req.agent, ...)
        transcript = [{"role": "user", "content": req.question}]

        for round in range(cfg.max_tool_rounds + 1):
            final_round = round == cfg.max_tool_rounds     # no tools: answer now
            turn = await workflow.execute_activity(
                llm_decide,
                LlmDecide(cfg, transcript, tools_enabled=not final_round),
                start_to_close_timeout=timedelta(seconds=180),
            )
            transcript.append(turn.raw)
            await workflow.execute_activity(persist_step, ...)
            if turn.final:
                await workflow.execute_activity(finalise_run, ...)
                return turn.answer

            results = []
            for call in turn.tool_calls:
                if call.name not in cfg.allowed_tools:            # privilege boundary
                    results.append(tool_error(call, f'Tool "{call.name}" is not permitted'))
                    continue
                if cfg.requires_approval(call.name):
                    await workflow.execute_activity(record_pending_approval, ...)
                    self._status = "awaiting_approval"
                    await workflow.wait_condition(lambda: call.id in self._decisions)
                    self._status = "running"
                    await workflow.execute_activity(record_decision, self._decisions[call.id], ...)
                    if not self._decisions[call.id].approved:
                        results.append(tool_error(call, "Rejected by a human reviewer"))
                        continue
                results.append(await workflow.execute_activity(
                    run_tool,
                    RunTool(call.name, call.args),
                    activity_id=f"{call.name}-{round}",   # keeps the Temporal UI legible
                    start_to_close_timeout=cfg.timeout_for(call.name),
                ))
            transcript.append({"role": "user", "content": results})

    @workflow.signal
    def submit_tool_decision(self, decision: ToolDecision) -> None:
        self._decisions[decision.tool_call_id] = decision

    @workflow.query
    def get_status(self) -> str:
        return self._status
```

Notes that belong in the code as comments:

- The workflow imports no SDK, reads no clock and generates no randomness. Everything non-deterministic is an activity. `activity_id` is available in the pinned `temporalio` 1.5.0.
- The final round runs with tools disabled so an agent that keeps calling tools still produces an answer instead of hitting the round cap silently.
- An unpermitted tool call returns an error *result* to the model rather than failing the run: the model can recover, and the refusal is recorded in the transcript.

### API Endpoints

FastAPI in `temporal/src/api.py`, deployed as a second compose service from the worker image (`uvicorn src.api:app --port 8000`).

```http
POST /agent/runs
{ "agent": "example_analyst", "question": "Which portfolios changed this week?" }
→ 202 { "run_id": "uuid", "workflow_id": "agent-uuid" }
```

```http
POST /agent/runs/{run_id}/decisions
{ "tool_call_id": "toolu_…", "approved": true, "decided_by": "jeevan" }
→ 204
```

```http
GET /agent/runs/{run_id}
→ 200 { "status": "awaiting_approval", "answer": null, "pending": { … } }
```

CORS is permissive for `http://localhost:3000` in local development, with a comment stating that production narrows it. Without this the first browser call fails, and the failure looks like an agent bug rather than a CORS bug.

### Frontend

Two engine additions, both filling in seams that already exist:

1. **Wire `customHandlers`.** `ActionDispatcher` already accepts a `customHandlers` registry and already dispatches `action: "custom"`, but `UIEngine` never passes one. Passing it (about five lines) enables two handlers, `startAgentRun` and `decideToolCall`, which POST to the agent API.
2. **`refetchInterval` on data sources.** About eight lines in `useDataSources`, so an in-flight run polls itself and stops when it completes.

`frontend/src/pages/agent.json` then renders the whole feature declaratively with components already in the registry — ask box (`Input` + `Button`), run list (`supabase` data source), message and tool trail (`each` over `agent_messages`), and an approval card (`Alert` plus approve/reject `Button`s) shown when `status = awaiting_approval`.

### Security Considerations

- The allowlist check happens in the workflow, above dispatch. No code path reaches a tool without it.
- A domain config cannot name a write activity, only a registry tool. Which activity implements a tool stays a framework decision (zonal records this as an ADR: packs declare action types, not executors).
- Write tools require approval unless explicitly opted out, per config rule 2.
- Tool arguments are validated against the tool's JSON Schema before dispatch; tools that query Supabase use the existing parameterised REST client rather than string-built SQL.
- The service-role key stays in the worker and the API service. The browser holds only the anon key and can read agent tables, never write them.
- Model output is treated as untrusted input: it selects a tool name and arguments, and both are checked before anything executes.

## Testing Strategy

The template currently ships no tests, so this is also the missing worked example.

- **Registry and config** — a YAML naming an unknown tool fails; a write tool without `requires_approval` fails; `allow_unapproved_writes` overrides it; `list_tools` renders only the named subset.
- **Adapters** — recorded provider payloads (Claude and Azure shapes) normalise to the same `AssistantTurn`; no network.
- **Workflow** — under Temporal's test environment with activities mocked: a read-only run completes; an unpermitted tool call returns an error result and the run continues; an approval pauses the run and the signal resumes it; a rejection is recorded as a tool error.
- **End to end with `provider: fake`** — the full loop, transcript persistence and approval path, with no credentials.

No frontend test runner is introduced; that gap is stated rather than filled.

## Implementation Plan

1. **Migration and persistence** — three tables, grants, `persist_step` activity, tests.
2. **Registry, config and the `fake` provider** — `@tool`, validation rules, one reference tool, hermetic tests.
3. **Workflow and activities** — `llm_decide`, `run_tool`, the loop, allowlist enforcement, workflow tests against `fake`.
4. **Claude adapter** — first-party plus Bedrock/Foundry client selection, adaptive thinking, refusal fallbacks, `output_config.format`.
5. **Approval path** — `record_pending_approval`, signal, status transitions, restart-survival check.
6. **API service** — FastAPI, compose service, CORS.
7. **Frontend** — `customHandlers` wiring, `refetchInterval`, `agent.json`, `/agent` route.
8. **Azure OpenAI adapter** — second adapter proving the seam.
9. **Documentation** — `docs/user-guide/agent.md`: how to add a tool, how to write an agent YAML, what may not go in a workflow, and where the non-goals would slot in.

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Transcript grows past Temporal's payload limits (2 MB per payload, 50 MB per history) | Run fails late, confusingly | Low locally, real in production | Truncate tool results in `run_tool`; persist the full transcript to `agent_messages`; document the ceiling |
| Determinism mistake in the workflow (`import anthropic`, `datetime.now()`) | Replay error with an opaque message | High for a first-time author | An explicit "what may not go in a workflow" section in the user guide, plus a decoder for the error message |
| CORS between browser and agent API | First browser call fails | Certain if unhandled | Permissive localhost CORS shipped, narrowed in production |
| Model answers in prose instead of the output schema | Run fails or returns nothing usable | Medium | Final round runs with tools disabled; prose is captured as the answer with a flag rather than raising |
| Model access unavailable on the day | Nothing runs | Medium | `provider: fake` runs the entire feature with no credentials |
| Editing workflow code while runs are in flight | Replay failure on existing runs | Medium in development | Documented: terminate old runs, or version the workflow; not solved by this feature |
| One retry policy across all tools | A slow tool times out or a fast one retries too eagerly | Low | Per-tool `timeout_seconds` and retry settings passed from the workflow, driven by the YAML |

## Open Questions

Resolved during implementation; recorded here because the reasoning outlived the question.

- **Config rule 2 (write tools must require approval unless explicitly opted out) — kept.** It is
  four lines in `config.py`, it fails with a message naming the tool, and `allow_unapproved_writes`
  keeps it from being a straitjacket.
- **The `fake` provider ships as a first-class adapter.** It is what makes the feature demonstrable
  and the test suite hermetic, and `AGENT_PROVIDER=fake` turned out to be more useful than
  anticipated: it exercises whatever tools a repo user has written, without a fixture.
- **Two reference tools ship, not one.** `list_entity_types` (read) and `record_note` (write). The
  second was added because the approval path is the most valuable thing here and, with only a read
  tool, nothing shipped could demonstrate it.

Still open:

- **No live model call has been made.** The Claude adapter's request translation, response
  normalisation and client selection are unit-tested, and `call()` is tested against a stub client,
  but no request has reached the Anthropic API from this repo — there were no credentials on the
  machine where it was built. The first person with a key should run one read-only question and
  confirm the answer satisfies `answer_schema`.
- **`azure_gpt` is likewise unproven against a live deployment**, for the same reason.

## Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-08 | Skeleton rather than a complete reference agent | Repo users learn tool design by writing tools |
| 2026-09-08 | Loop in the workflow, not inside one activity | Per-tool retry and idempotency; approval as a signal; visible history |
| 2026-09-08 | One generic `run_tool` activity | Adding a tool stays a one-file change |
| 2026-09-08 | Python registry + per-agent YAML | Framework owns implementations; config only narrows |
| 2026-09-08 | Provider seam with a neutral `AssistantTurn` | Azure/Bedrock/Foundry agnosticism without a vendor abstraction layer |
| 2026-09-08 | `output_config.format` instead of a forced submit tool | Supported alongside tools by the current API; removes a workaround zonal still carries |
| 2026-09-08 | Scheduled runs out of scope | Keeps the first version to one triggering path |
| 2026-09-08 | `AGENT_PROVIDER` env override added | One shipped config runs offline in development and against a real model in a deployment, without duplicating the file |
| 2026-09-08 | `refetchWhile` evaluates against the source's own `result` | Page `data` is assembled after the queries are built, so a polling source can only see its own last value |
| 2026-09-08 | `content::text` in the page's select rather than a new column | PostgREST casts jsonb to text, so the transcript renders without a schema change or a new component |
| 2026-09-08 | Activities referenced by name from the workflow | Lets the worker inject Supabase-backed implementations and tests inject recorders, with one loop |
| 2026-09-08 | No refusal fallbacks | The pinned SDK (anthropic 1.4.0) exposes them on its beta namespace; a template should not sit on a beta surface. A refusal fails the run with a readable reason |

## References

- `zonal-ops-agent`: `temporal/src/activities/llm_agent.ts` (provider-agnostic LLM activity, tool dispatch, schema enforcement)
- `zonal-ops-agent`: `temporal/src/workflows/pack_tools.ts` and `temporal/packs/schemas/tools.schema.json` (allowlists, narrow-not-widen)
- `zonal-ops-agent`: `docs/adrs/packs-declare-action-types-not-executors.md`
- This repo: `docs/runbooks/local-startup.md` (running stack, ports, known issues)
- This repo: `supabase/migrations/20260908090000_entity_operations.sql` (grant posture this feature follows)
