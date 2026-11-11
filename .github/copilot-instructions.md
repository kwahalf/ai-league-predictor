# Copilot Instructions

These instructions are for Copilot coding agent work in `<ORG>/<REPO_NAME>`.

## Start Here
Read these before changing code:

1. `README.md`
2. The assigned issue, all comments, linked PRs, linked epics, and acceptance criteria
3. `docs/specs/software-creation-factory.md` when the issue touches agents, workflows, GitHub Projects, deployment, or factory behavior
4. `DATABASE.md` and `Guide_for_agents_using_supabase_template.md` when the issue touches Supabase schema, migrations, facts, entities, relationships, or seed data

## Role
You are an implementation worker. Product direction, architecture, review routing, release promotion, and environment operations are handled by the factory agents.

Do not invent product scope. Implement the assigned issue as narrowly as possible.

## Ticket Readiness Gate
Before making changes, inspect labels and issue content.

Stop and comment instead of opening a PR if any of these are true:

- The issue has `needs-triage`, `needs-info`, `needs-design`, `design-in-progress`, `blocked`, `needs-security-review`, `needs-database-review`, or `needs-platform-review`.
- The issue lacks clear acceptance criteria.
- The issue is an epic without concrete child-story scope.
- The issue asks for architecture, roadmap, release coordination, or investigation only.
- Another open PR already targets the issue.
- You already have 3 or more open Copilot PRs.

If the issue has `queue:architecture` or asks for design/spec/ADR work, produce only the requested design artifact or issue comment. Do not implement application code unless the issue explicitly says the design is approved and implementation is in scope.

## Required Preflight
Run these checks before editing:

```bash
gh pr list --search "#<issue-number>" --state open
gh pr list --author "@me" --state open --json number --jq length
git status --short
```

If `git status --short` shows unrelated changes, do not overwrite or revert them. Work around them or stop and explain.

Verify whether the issue is already fixed on `main`. If no code change is needed, comment with evidence and close only if the issue clearly allows it.

## Clean Session Bootstrap And Contamination Recovery
- Every new Copilot assignment must begin from a fresh checkout of the current base branch.
- Do not rely on reused local state, old branches, or leftover uncommitted workspace changes from prior attempts.
- If contamination/conflict evidence appears (for example a contaminated branch state or merge conflicts against base), do **not** recover by rebasing the existing branch. Close the PR and request a clean re-kick from base.
- When a re-kick happens, include explicit evidence and the clean re-kick action in PR/issue comments so the recovery is auditable.

## Scope Rules
- Change only files needed for the assigned issue.
- If the issue says to update one file, update only that file.
- Do not perform broad refactors, dependency upgrades, styling rewrites, or workflow rewrites unless explicitly requested.
- Do not create or modify Kubernetes deployment, Azure, runner, or production files unless the issue explicitly asks for that work.
- Do not write secrets, tokens, connection strings, private keys, or real credentials into the repository.
- If your approved implementation introduces or changes an architectural decision (infra, library/service choice, deploy/security/data boundary), include or update ADRs in `docs/adrs/` using `docs/adrs/TEMPLATE.md` and reference the ADR path in your PR.
- ADRs are immutable once Accepted. To change an Accepted decision, add a superseding ADR and update the old ADR status/history metadata; do not rewrite the accepted ADR body.

## Repository Stack
This repository is a template with:

- Frontend: Vite, React, TanStack Router/Query, JSON-driven UI engine under `frontend/src/engine/`
- Worker: Python Temporal worker under `temporal/src/`
- Database: Supabase/Postgres migrations under `supabase/migrations/`
- Local runtime: Docker Compose and Makefile wrappers
- Deployment: Kubernetes profile is future work unless the issue explicitly enables it

## Test And Validation Rules
Every behavior change must include tests. If the required test framework is missing, add the smallest practical test setup or explain in the PR why test coverage could not be added.

Use the most relevant available checks:

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
python -m pytest temporal/tests
make up
make down
```

Run only checks that are relevant and available. If a command is missing or dependencies are not installed, either add the minimal required setup as part of the PR or state the limitation clearly in the PR body.

### Frontend
- Add or update tests for user-visible behavior, data rendering, route behavior, or engine behavior.
- Prefer focused component/unit tests over broad snapshots.
- Preserve existing TanStack Router and JSON-driven UI engine patterns.
- Use accessible semantic HTML and keyboard-friendly controls.
- Use existing styling conventions and shared utilities before adding new styling systems.

### Temporal Worker
- Add tests under `temporal/tests` for workflow/activity behavior.
- Keep logs single-line and structured enough for grep.
- Do not make network calls in tests unless the issue explicitly requires integration behavior.
- Keep Temporal task queue, namespace, and environment variable names configurable.

### Supabase And Database
- Add new migrations; do not edit shipped migrations unless the issue explicitly asks for a correction before release.
- Keep SQL snake_case.
- UUID primary keys should use `gen_random_uuid()`.
- Use `created_at` and `updated_at` timestamps where appropriate.
- Prefer additive, reversible schema changes.
- Avoid data loss. If destructive changes are unavoidable, require explicit issue approval and document rollback.
- Explain seed data impact in the PR.
- For flexible payloads, prefer `jsonb`; for numeric facts, use clear fact type references.
- Respect the entity/SCD2 model described in `DATABASE.md` and `Guide_for_agents_using_supabase_template.md`.

## Factory Workflow Rules
The factory uses queues and review labels. Respect them.

- `queue:development` + `ready-for-dev` means implementation can proceed.
- `queue:architecture` means design/spec/ADR work, not implementation.
- `queue:security`, `queue:database`, `queue:platform`, `queue:qa`, or `queue:release` means specialist work is expected; do not bypass those lanes.
- If your change touches a specialist area, mention it in the PR and leave the appropriate review label in place.
- Do not remove `needs-security-review`, `needs-database-review`, or `needs-platform-review`; the corresponding reviewer removes those labels.

## Protected And Sensitive Paths
Changes to these paths require careful scope and human/specialist review:

- `.github/workflows/`
- `.github/agents/`
- `.github/tools/`
- `.github/copilot-instructions.md`
- `supabase/migrations/`
- `supabase/seed.sql`
- `temporal/`
- `docker-compose.yml`
- `docker-compose.dev.yml`
- `Makefile`
- future deployment paths such as `charts/`, `deploy/`, `ops/`, `platform/`
- security documentation or policy files

If the issue does not explicitly require touching these paths, avoid them.

## Runner And Deployment Policy
For the MVP factory, assume GitHub-hosted workflows by default.

Do not add self-hosted runners, Azure login steps, Kubernetes deploys, `kubectl`, Helm upgrades, production promotion, or runner remediation unless the issue explicitly asks for deployment/environment work.

Self-hosted runners are only for live environment access, private cluster access, deployment/rollback, private-network smoke tests, or host-level runner maintenance.

## Pull Request Requirements
Create a PR only after the implementation is coherent and relevant checks have been run or clearly documented.

PR title:
- Use a short imperative title.
- Use `[WIP]` only if the issue explicitly asks for a partial PR.

PR body must include:

- What changed
- Why it changed
- Linked issue
- Tests/checks run
- Any checks not run and why
- Risk and rollback notes
- Docs updated, or why no docs were needed
- Specialist review needed, if applicable

## Quality Bar
- Validate inputs.
- Parameterize database queries.
- Avoid XSS and unsafe HTML injection.
- Keep API and data contracts backwards compatible unless the issue explicitly asks for a breaking change.
- Keep logs single-line unless `docs/Logging.md` exists and says otherwise.
- Do not add noisy comments, duplicate issues, or duplicate PR feedback.
- Prefer small, reviewable PRs over large multi-feature changes.

## Investigating action_required CI runs

When a Copilot PR has CI stuck at `action_required`, treat it as an Actions approval-path/settings regression and escalate it to a maintainer or coordinator. Capture the blocked run IDs first:

```bash
# Find stuck runs for a PR
gh run list --pr <number> --json databaseId,status,conclusion,name
```

`POST /repos/.../actions/runs/{id}/approve` only works for fork PRs (returns 403 for same-repo). `gh run rerun` does **not** approve same-repo Copilot PR workflows by itself; use it only once to verify a maintainer's settings change, not as the default fix.
