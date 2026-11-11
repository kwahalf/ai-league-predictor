-- Agent run state: one row per run, one row per transcript entry, one row per
-- approval decision. See docs/specs/2026-09-08-agentic-feature.md.
--
-- Read/write asymmetry is deliberate: the browser reads a run's transcript, and
-- only the worker (service_role) writes it. A model-driven loop that could be
-- edited from the browser would have no audit value.

create table agent_runs (
  id uuid primary key default gen_random_uuid(),
  agent text not null check (btrim(agent) <> ''),
  question text not null check (btrim(question) <> ''),
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
  seq integer not null check (seq >= 0),
  role text not null check (role in ('user', 'assistant', 'tool')),
  content jsonb not null,
  tool_name text,
  tokens_in integer,
  tokens_out integer,
  created_at timestamptz not null default now(),
  -- Makes persist_step idempotent: a retried activity re-inserts the same
  -- (run, seq) and is ignored rather than duplicating a transcript entry.
  unique (run_id, seq)
);

create table agent_approvals (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  -- The provider's own tool-call id, so a decision binds to one call and not to
  -- a tool name that may be called more than once in a run.
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

create index idx_agent_runs_status on agent_runs(status, created_at desc);
create index idx_agent_messages_run on agent_messages(run_id, seq);
create index idx_agent_approvals_pending on agent_approvals(run_id) where status = 'pending';

create trigger set_timestamp_agent_runs
  before update on agent_runs
  for each row execute function update_updated_at();

-- The browser reads; the worker writes.
alter table agent_runs enable row level security;
alter table agent_messages enable row level security;
alter table agent_approvals enable row level security;

revoke all on agent_runs, agent_messages, agent_approvals from anon, authenticated;
grant select on agent_runs, agent_messages, agent_approvals to anon, authenticated;
grant all on agent_runs, agent_messages, agent_approvals to service_role;

-- RLS is on, so a select through PostgREST needs a policy even with the grant.
-- Local development exposes every run to the anon key, matching the rest of this
-- template's unauthenticated posture; a real deployment scopes these by owner.
create policy agent_runs_read on agent_runs for select to anon, authenticated using (true);
create policy agent_messages_read on agent_messages for select to anon, authenticated using (true);
create policy agent_approvals_read on agent_approvals for select to anon, authenticated using (true);

notify pgrst, 'reload schema';
