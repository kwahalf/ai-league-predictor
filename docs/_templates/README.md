# Documentation templates

Fill-in-the-blank scaffolding for the docs a project tends to need. **Copy** a
template out of here into `docs/` when you actually need it, then customise it —
delete sections that don't apply, add ones that do. These files are starters,
not living docs; the real docs live one level up in `docs/`.

Some doc types have a dedicated home rather than a template here:

- **Architecture** → write it in [`docs/architecture/`](../architecture/) (start from its README).
- **Decisions (ADRs)** → use [`docs/adrs/TEMPLATE.md`](../adrs/TEMPLATE.md) and the process in [`docs/adrs/`](../adrs/).
- **Feature specs** → use [`docs/specs/TEMPLATE.md`](../specs/TEMPLATE.md).
- **User guides** → [`docs/user-guide/`](../user-guide/).

## What's here

| Template | Use when you need to document… |
|---|---|
| [API.md](./API.md) | Endpoints, auth, request/response, errors, webhooks |
| [DATABASE.md](./DATABASE.md) | Schema, relationships, indexes, RLS, common queries |
| [BUILD.md](./BUILD.md) | Local setup, build commands, CI/CD basics |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Environments, deploy methods, migrations, rollback |
| [TESTING.md](./TESTING.md) | Test strategy, types, organisation, coverage goals |
| [LOGGING.md](./LOGGING.md) | Log levels, format, locations, aggregation |
| [MONITORING.md](./MONITORING.md) | Metrics, dashboards, alerting, SLOs/SLIs, tracing |
| [PERFORMANCE.md](./PERFORMANCE.md) | Perf goals, benchmarks, load testing, optimisation |
| [SECURITY.md](./SECURITY.md) | Vuln reporting, authz, data protection, compliance |
| [RUNBOOK.md](./RUNBOOK.md) | On-call procedures, incident response, recovery |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Common problems and their fixes |
| [features/payment-processing.md](./features/payment-processing.md) | Worked example of a complex-feature deep dive |

## Conventions

- Keep docs in the repo, in Markdown, versioned alongside the code they describe.
- Prefer Mermaid for diagrams (renders on GitHub).
- Cite evidence — file paths, PR numbers, live resource names — over prose.
