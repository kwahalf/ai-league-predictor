# The agent

An agent here is a Temporal workflow that asks a model what to do, checks the
answer against an allowlist, runs the tool, feeds the result back, and repeats
until the model produces an answer. Tools that write pause the run until a human
approves them.

Nothing about your domain lives in the loop. You supply tools and prompts.

## Run it now, with no credentials

`AGENT_PROVIDER=fake` replaces the model with a stand-in
that calls each offered tool once and then answers. It needs no API key and no
network, and it exercises the real loop, the real tools, the real approval step
and the real transcript.

```sh
make up
open http://localhost:3000/agent
```

Ask *"Note that the review is done."* and you will see the agent call
`list_entity_types`, then stop and ask permission to call `record_note`. Approve
it, and the run finishes. Watch the same run in the Temporal UI
(http://localhost:8080) to see each model turn and each tool call as its own
durable step.

To use a real model, leave `AGENT_PROVIDER` empty and set the credentials for
whichever provider the agent config names. **The default provider is `litellm`**, so
an agent that names none goes through the proxy — start it with
`docker compose --profile litellm up -d litellm` and declare its model aliases in
`litellm/config.yaml`.

## The pieces

| File | What it is |
| --- | --- |
| `temporal/src/workflows/agent/agent_workflow.py` | The loop. Domains never edit it. |
| `temporal/src/activities/agent.py` | Everything the loop is not allowed to do itself. |
| `temporal/src/agent/tools.py` | **Your tools.** Two examples ship. |
| `temporal/agents/*.yaml` | **Your agents.** One example ships. |
| `temporal/src/agent/llm/` | Provider adapters — `claude`, `azure_gpt`, `litellm`, `fake`. The OpenAI wire format lives once in `openai_chat.py`. |
| `frontend/src/pages/agent.json` | The page, as JSON like every other page. |
| `frontend/src/routes/agent.tsx` | The three handlers that talk to the agent API. |

## Adding a tool

Write a function in `temporal/src/agent/tools.py`:

```python
@tool(
    name="search_entities",
    description="Find entities of a type whose name matches a query.",
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {"type": "string"},
            "query": {"type": "string"},
        },
        "required": ["entity_type", "query"],
        "additionalProperties": False,
    },
    writes=False,
)
def search_entities(entity_type: str, query: str) -> dict:
    rows = request(
        f"entities?entity_type=eq.{entity_type}&select=id,entity_versions(data)",
        method="GET",
    )
    return {"entities": rows or []}
```

Name it in an agent's YAML, then restart the worker:

```sh
docker compose restart temporal-worker
```

That is the whole procedure. You do not register the tool with the worker, touch
the workflow, or change the frontend.

Four things the framework does for you:

- **Arguments are validated** against `input_schema` before your function runs,
  so the values you receive match what you declared.
- **Results are truncated** before they reach the model, so a large result
  degrades the answer instead of breaking the run.
- **Exceptions become tool errors**, not failed runs — the model reads the error
  and can try something else, and the attempt stays in the transcript.
- **`writes=True` is enforced upward**: an agent granting a writing tool must
  pair it with `requires_approval: true`.

### Writing good tool descriptions

The description is not documentation, it is the only thing the model has when
deciding whether to call your tool. Say what it returns and when to use it. "Use
this first to discover what kinds of records exist" changes behaviour; "lists
entity types" does not.

## Adding an agent

Copy `temporal/agents/example_analyst.yaml`. The filename must match the `name`
field, because that is how a run asks for it.

```yaml
name: my_agent
provider: litellm         # litellm (default) | claude | bedrock | foundry | vertex | azure_gpt | fake
model: claude-opus-5
max_tool_rounds: 6
system_prompt: |
  ...
answer_schema:            # must be an object with additionalProperties: false
  type: object
  properties:
    summary: { type: string }
  required: [summary]
  additionalProperties: false
tools:
  - name: search_entities
  - name: record_note
    requires_approval: true
    timeout_seconds: 30
```

Four rules are checked when the run starts, and each failure names the offender:

1. **Narrow only.** Every tool must exist in the registry. A config chooses among
   the framework's tools; it cannot define one, change its behaviour, or name the
   activity behind it. This is a privilege boundary, not a convenience.
2. **No escalation by omission.** A `writes=True` tool needs
   `requires_approval: true`, unless you set `allow_unapproved_writes: true` on
   the agent and mean it.
3. **A closed answer schema**, because that is what a strict output format needs.
4. **At least one tool.** An agent that can call nothing is not an agent.

## What may not go in the workflow

Temporal replays the workflow to recover it, so the loop must give the same
answers every time it is replayed. In `agent_workflow.py` you may not use:

- a model SDK, an HTTP call, or a file read
- `datetime.now()`, `random`, `uuid4`
- a tool implementation

All of it belongs in an activity. If you break this rule the error looks like
this, usually on the second run rather than the first:

```
Nondeterminism error: workflow ... did not match ...
```

Read it as: *the code changed, or it asked the world something during replay.*

Two related habits:

- **Editing workflow code with runs in flight** breaks their replay. In
  development, terminate old runs (`docker compose restart temporal-worker`
  will not do it — use the Temporal UI) rather than debugging the replay error.
- **Adding a tool never has this problem**, because tools live behind an
  activity. This is why the framework is arranged the way it is.

## Human approval

A tool with `requires_approval: true` makes the run pause:

1. The workflow writes a pending row to `agent_approvals` and sets the run's
   status to `awaiting_approval`.
2. The page shows the tool name and the exact arguments proposed.
3. Approve or reject sends a Temporal **signal** to the workflow.
4. The workflow records the decision and either runs the tool or feeds a
   rejection back to the model as a tool error.

The run holds indefinitely and survives a worker restart — try it: approve
nothing, run `docker compose restart temporal-worker`, then approve. There is no
timeout by design; add a `workflow.timeout` in `_await_decision` if your
deployment wants runs to expire.

## Providers

| `provider` | Reads |
| --- | --- |
| `claude` | `ANTHROPIC_API_KEY` |
| `bedrock` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| `foundry` | Microsoft Foundry credentials (Claude on Azure) |
| `vertex` | GCP application default credentials |
| `azure_gpt` | `AZURE_OPENAI_BASE_URL` (resource root), `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_API_STYLE` — `deployments` (default) or `v1` |
| `litellm` | `LITELLM_BASE_URL`, and `LITELLM_API_KEY` if the proxy requires one |
| `fake` | nothing |

The first four share one code path: Claude exposes the same request surface
wherever it is hosted, so switching is a client-construction choice, not an
adapter. `azure_gpt` is a real adapter, because the OpenAI chat-completions
format differs in four ways (tool declarations, tool-call arguments as a JSON
string, one message per tool result, and a different output-schema field).

The canonical transcript is the Anthropic message shape. Every adapter converts
to and from it, so the loop above the seam never knows which provider ran.

A config that names no `provider` gets `litellm`, so model choice, spend caps and
rate limits are decided in one place rather than per agent. Naming a provider is
then a deliberate bypass of that, which is the right way round.

`AGENT_PROVIDER` overrides every config's `provider`, which is how one shipped
agent runs offline in development and against a real model in a deployment.

## Tests

```sh
docker compose run --rm -v "$PWD/temporal:/app" --entrypoint python \
  temporal-worker -m pytest tests -q
```

The suite needs no API key. The loop tests run against the Temporal server in the
compose stack and skip themselves if it is not up, so the suite passes on a
machine with nothing running.

Worth reading as examples: `tests/test_agent_workflow.py` (the loop, the
allowlist, approval and rejection end to end) and `tests/test_llm_claude.py`
(request translation and response normalisation as pure functions).

## Not included

Each of these was left out deliberately; the note says where it would go.

- **Streaming tokens to the browser.** The transcript is the artifact; runs are
  polled. Streaming would need the model call to stop being one activity.
- **Scheduled or data-triggered runs.** Start the same workflow from a Temporal
  schedule, or from a database trigger, instead of from the API.
- **Multi-agent delegation.** A tool whose implementation starts a child
  workflow is the natural shape.
- **Retrieval and embeddings.** A tool, like any other.
- **Authentication and per-user scoping.** The template has no auth; the agent
  tables are readable by the anon key in local development, and a deployment
  would scope them by owner in the RLS policies.
- **Server-side refusal fallbacks.** The pinned SDK exposes them on its beta
  namespace; a refusal is currently reported and fails the run with a readable
  reason instead.
