# Architecture Decision Records (ADRs)

This directory records the significant architectural decisions made on this project, **why** they were made, and what we traded away. ADRs are the reference point for reviews: when we evaluate a change, a spec, or a deploy, we check it against these records to confirm we are still making the right decisions — and we add or supersede a record when we make a new one.

## Why we keep these

Architectural decisions (data model, orchestration engine, deployment topology, external edge, auth boundaries) are easy to execute in code and impossible to review later if they were never written down. That is the failure mode these records exist to prevent: a review has nothing to check against if the decision was never articulated. Record the decision when you make it, not after something built on top of it breaks.

## Process

- **One decision per file**, named `NNNN-short-slug.md`, numbered sequentially.
- Use [`TEMPLATE.md`](./TEMPLATE.md). Keep each record short and concrete; cite **evidence** (commit hashes, PR numbers, file paths, live resource names).
- **Status** is one of: `Proposed` · `Accepted` · `Superseded by ADR-NNNN` · `Deprecated`.
- An ADR is **immutable once Accepted** — to change a decision, write a new ADR that supersedes it and update the old one's status. Do not silently rewrite history.
- **When to write one:** any decision that is costly to reverse, shapes more than one component, picks one technology/pattern over alternatives, or changes a security/data/deploy boundary. When in doubt, write it.
- **Who:** the Factory Architect owns ADR authorship for designs it produces; the Tech Reviewer should flag any PR that makes an architectural decision without a corresponding ADR (see the maintenance note at the bottom).

## Index

No ADRs recorded yet. Add a row per decision as you accept it.

| ADR | Title | Status |
|---|---|---|
| [0001](./0001-example-slug.md) | _First decision title_ | Proposed |

## Maintenance note

Keeping this index honest is the whole point. Factory policy enforcement:
- Tech Reviewer ADR-gate: a PR that adds/changes infra, swaps a library/service, introduces a new service, or changes a deploy/security/data boundary must link an ADR (or `docs/adrs/`) in the PR. If missing, request changes and add `needs-adr`.
- Factory Architect ADR authorship: when an architecture design/spec introduces or changes a decision, the Architect publishes the corresponding ADR(s) in `docs/adrs/` using `TEMPLATE.md`.
- Copilot implementation rule: when approved implementation changes introduce an architectural decision, include/update the ADR in `docs/adrs/` and reference it in the PR.
- Accepted ADRs are immutable. Changed decisions must be recorded via a new superseding ADR plus status/history updates to the prior ADR.
