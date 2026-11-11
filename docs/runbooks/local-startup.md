# London template local startup

Status: running and checked on 2026-09-08.

Working copy: `/Users/jeevan/Projects/volaris-london-template`, extracted from the supplied `project-template-main.zip`. The source archive is unchanged.

## Access

| Service | URL / address |
| --- | --- |
| Frontend | http://localhost:3000 |
| Agent page | http://localhost:3000/agent |
| Agent API | http://localhost:8000 |
| Temporal UI | http://localhost:8080 |
| Temporal gRPC | localhost:7234 |
| Supabase API | http://localhost:54321 |
| Supabase Studio | http://localhost:54323 |
| Test email inbox | http://localhost:54324 |
| Supabase Postgres | localhost:54322 |
| Temporal Postgres | localhost:5433 |

## Start and stop

From this directory, run `make up` to start, `make down` to stop, or `make logs` to inspect logs. `USE_DEV=1 make up` enables source mounts and frontend live reload. Use `docker compose up -d --build` with the live key exports when rebuilding images; the simpler full command is:

```sh
supabase start
exports="$(./scripts/supabase-env.sh)" && eval "$exports" && docker compose up -d --build --wait
```

`make reset` deletes this template's database data. It is not needed for normal restarts.

The Supabase and Compose project names are `volaris-template-london`. Compose-generated names and separate volumes prevent collisions with older template installations. Compose host ports bind to loopback.

The old `mna-app` Supabase containers and `mna-temporal` Compose containers were stopped with `docker stop`, with user authorization. Their containers and volumes were preserved. Other processes and projects were left alone. Stop this template before restarting the old app because they use the same host ports.

## Local startup fixes

- Replaced the Temporal `true` healthcheck with `tctl --address temporal:7233 cluster health` and changed dependent services to wait for health. The original worker initially crashed with connection refused before Temporal finished starting.
- `make up` now stops if Supabase key discovery fails and uses Compose `--wait` before reporting success.
- Added HTTP localhost Auth redirects matching the local frontend.
- Created `.env` from the example; `make up` injects instance-specific keys at runtime.
- Matched `max_concurrent_activities` to the worker's thread-pool size, removing the startup warning.

## Template defects fixed

1. **Entity write RPCs were missing.** Added `supabase/migrations/20260908090000_entity_operations.sql`: `create_entity_with_version`, `update_entity_with_version` (SCD2), `append_entity_event`, `create_entity_relationship`, and a `notifications` table. All are `security invoker`, idempotent per Temporal activity attempt, and the worker-only functions plus `notifications` are restricted to `service_role`.
2. **Worker activities were stubs.** `temporal/src/activities/` now calls Supabase over PostgREST with the service-role key (`client.py`), and `send_email` delivers over SMTP to the local mail catcher. Retries reuse a deterministic id derived from the workflow run and activity id, so a retried activity does not create a duplicate row.
3. **Lint could not run.** `eslint-plugin-react` and the TypeScript ESLint packages were missing, and `eslint .` matched no files because no extensions were configured. `.eslintrc.cjs` now uses `@typescript-eslint/parser` with the React, hooks and TypeScript configs, and `npm run lint` passes `--ext`. Result: 0 errors, 3 `react-refresh` warnings from the shadcn components.
4. **Hooks were called after early returns** in `ComponentRenderer`, so a component whose `if` condition flipped could crash React with a changed hook order. The registry component now renders through an inner `ResolvedComponent` that owns the hooks.
5. **Actions inside `each` loops lost their row.** `{{entity.id}}` resolved to nothing at dispatch time, so the list's **View** button navigated to `/entities/portfolio/` and nothing happened. `UIEngineScopeContext` now publishes the loop bindings, and `useUIEngine()` merges them into `dispatch`/`evaluateExpression`, so every action component picks them up without changes.
6. **`npm run typecheck` (added) had 18 errors.** Added `src/vite-env.d.ts` for `import.meta.env`, fixed the Postgrest builder type in `queryBuilder.ts`, replaced the non-existent `NavigateFunction` import with `UseNavigateResult`, made `EngineLink`'s `to` optional (JSON may omit it) and limited external hrefs to http/https/mailto/relative targets, and removed `vite.config.ts` from the app tsconfig's `include` (TS6305).
7. **Console noise.** `each` no longer warns while a data source is still loading, missing single-row records use `maybeSingle()` instead of returning HTTP 406, and modals without a description no longer trigger the Radix `aria-describedby` warning.

## Verification

- All three migrations applied and the empty seed ran.
- All 12 started Supabase containers stayed up; all services with healthchecks reported healthy. Image proxy and pooler are not enabled in this template.
- All six Compose services ran (frontend, temporal, temporal-db, temporal-ui, temporal-worker, agent-api); both databases and Temporal reported healthy.
- Frontend, Temporal UI, Supabase Studio, and Auth health endpoint returned HTTP 200.
- Browser CRUD against the container build on http://localhost:3000: created an entity from the dialog, opened its detail page through **View**, toggled inline edit, deleted it through the confirmation modal and landed back on the list. No HTTP >= 400 responses and no console errors or warnings in any of these runs.
- A record that does not exist (deleted, or a bad id in the URL) renders an empty detail page instead of erroring.
- Every worker activity ran against real infrastructure: `create_entity`, `update_entity_scd2` (`get_entity` then returned version 2), `append_event`, `create_relationship`, `send_notification` (row in `notifications`) and `send_email` (message visible in the mail catcher).
- The approval workflow was started, queried (`pending_approval`), signalled and completed with `approved` after the worker rebuild.
- `npm run lint` (0 errors), `npx tsc --noEmit` (clean), `npm run build` and `python3 scripts/audit/run_audits.py` (no findings) all pass.

## The agent feature

Implemented on 2026-09-08 per `docs/specs/2026-09-08-agentic-feature.md`; usage in
`docs/user-guide/agent.md`.

`AGENT_PROVIDER=fake` is set in `.env`, so the agent runs end to end with no model
credentials: `http://localhost:3000/agent`, ask "Note that the review is done.",
approve the `record_note` call when it pauses. The run appears in the Temporal UI
as discrete model turns and tool calls.

Run its tests with:

```sh
docker compose run --rm -v "$PWD/temporal:/app" --entrypoint python \
  temporal-worker -m pytest tests -q
```

Verified: 84 tests pass; a run started through the API completed and persisted its
transcript; the `record_note` write tool paused for approval in the browser,
resumed on approval and created a `note` entity; the rejection path feeds an error
back to the model instead of running the tool.

`scripts/audit/check_temporal_registration.py` was taught to see activities registered
through a factory (`*build_activities()`), including under an import alias. Before that
it reported the seven agent activities as unregistered, which was a blind spot rather
than a gap — it still catches a genuinely unregistered activity, verified with a
throwaway probe.

Not verified: no live model call has been made from this repo — there were no
Anthropic or Azure credentials on this machine, so the `claude` and `azure_gpt`
adapters are unit-tested only.

## Known remaining items

1. **Test coverage is uneven.** `temporal/tests/` now holds 84 tests, all added with the agent feature, so the agent is covered but the template's original activities and workflow are not. The frontend still has no test runner, so every frontend check above was a scripted browser run.
2. **`@tanstack/router-devtools` is deprecated** and logs a console warning asking for `@tanstack/react-router-devtools`. Left as-is because it is a dependency swap rather than a defect.
3. **Three `react-refresh/only-export-components` lint warnings** come from the shadcn `badge`/`button` variant exports and `renderComponents`. They only affect hot-reload granularity.
4. **Smoke-test rows are still in the database** (a few `portfolio`/`holding` entities plus one event, relationship and notification). `make reset` clears them; a targeted cleanup was blocked by the local tool permission policy.

The database seed is intentionally empty. This is a running development scaffold; the checks above do not establish that all application features are implemented.
