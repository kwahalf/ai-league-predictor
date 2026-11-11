-- Atomic entity writes shared by the UI and Temporal activities.
-- Invoker rights preserve the caller's existing table permissions and RLS.
create function public.create_entity_with_version(
  p_entity_type text, p_data jsonb, p_entity_id uuid default gen_random_uuid()
) returns jsonb language plpgsql security invoker set search_path = public as $$
declare v_version_id uuid;
begin
  if p_entity_type is null or btrim(p_entity_type) = '' or jsonb_typeof(p_data) is distinct from 'object' then
    raise exception 'Entity type and object data are required' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_entity_id::text, 0));
  select id into v_version_id from entity_versions where entity_id = p_entity_id and version_number = 1;
  if v_version_id is null then
    insert into entities(id, entity_type) values(p_entity_id, p_entity_type);
    insert into entity_versions(entity_id, version_number, data)
      values(p_entity_id, 1, p_data) returning id into v_version_id;
  end if;
  return jsonb_build_object('entity_id', p_entity_id, 'version_id', v_version_id, 'success', true);
end;
$$;

create function public.update_entity_with_version(
  p_entity_id uuid, p_data jsonb, p_version_id uuid default gen_random_uuid()
) returns jsonb language plpgsql security invoker set search_path = public as $$
declare v_number integer;
begin
  if jsonb_typeof(p_data) is distinct from 'object' then
    raise exception 'Object data is required' using errcode = '22023';
  end if;
  -- Serialize concurrent versions of the same entity.
  perform 1 from entities where id = p_entity_id for update;
  if not found then raise exception 'Entity not found' using errcode = 'P0002'; end if;
  if not exists(select 1 from entity_versions where id = p_version_id and entity_id = p_entity_id) then
    select coalesce(max(version_number), 0) + 1 into v_number from entity_versions where entity_id = p_entity_id;
    insert into entity_versions(id, entity_id, version_number, data)
      values(p_version_id, p_entity_id, v_number, p_data);
    update entities set updated_at = now() where id = p_entity_id;
  end if;
  return jsonb_build_object('entity_id', p_entity_id, 'version_id', p_version_id, 'success', true);
end;
$$;

-- Notifications are durable worker output. No anonymous access is granted.
create table public.notifications (
  id uuid primary key,
  user_id uuid not null,
  message text not null check (btrim(message) <> ''),
  created_at timestamptz not null default now(),
  read_at timestamptz
);
alter table public.notifications enable row level security;
revoke all on public.notifications from anon, authenticated;
grant all on public.notifications to service_role;

create function public.append_entity_event(
  p_entity_id uuid, p_entity_type text, p_event_type text, p_data jsonb,
  p_event_id uuid, p_actor_id text default null, p_correlation_id text default null
) returns boolean language plpgsql security invoker set search_path = public as $$
declare v_fact_id uuid;
begin
  if p_event_type is null or btrim(p_event_type) = '' or jsonb_typeof(p_data) is distinct from 'object' then
    raise exception 'Event type and object data are required' using errcode = '22023';
  end if;
  if not exists(select 1 from entities where id = p_entity_id and entity_type = p_entity_type) then
    raise exception 'Entity not found or type mismatch' using errcode = 'P0002';
  end if;
  insert into fact_types(key, label) values ('event:' || p_event_type, p_event_type)
    on conflict(key) do update set key = excluded.key returning id into v_fact_id;
  insert into time_series_points(id, entity_id, fact_type_id, observed_at, data_payload, metadata)
    values(p_event_id, p_entity_id, v_fact_id, now(), p_data,
      jsonb_build_object('actor_id', p_actor_id, 'correlation_id', p_correlation_id))
    on conflict(id) do nothing;
  return true;
end;
$$;

create function public.create_entity_relationship(
  p_parent_id uuid, p_child_id uuid, p_relationship_type text,
  p_metadata jsonb, p_relationship_id uuid
) returns jsonb language plpgsql security invoker set search_path = public as $$
begin
  if p_relationship_type is null or btrim(p_relationship_type) = '' or jsonb_typeof(p_metadata) is distinct from 'object' then
    raise exception 'Relationship type and object metadata are required' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_parent_id::text || p_child_id::text || p_relationship_type, 0));
  -- Check before insert: a retry must not invoke the SCD2 trigger again.
  if not exists(select 1 from relationships_v2 where id = p_relationship_id) then
    insert into relationships_v2(id, parent_id, child_id, relationship_type, metadata)
      values(p_relationship_id, p_parent_id, p_child_id, p_relationship_type, p_metadata);
  end if;
  return jsonb_build_object('relationship_id', p_relationship_id, 'success', true);
end;
$$;

revoke all on function public.append_entity_event(uuid,text,text,jsonb,uuid,text,text) from public;
revoke all on function public.create_entity_relationship(uuid,uuid,text,jsonb,uuid) from public;
grant execute on function public.append_entity_event(uuid,text,text,jsonb,uuid,text,text) to service_role;
grant execute on function public.create_entity_relationship(uuid,uuid,text,jsonb,uuid) to service_role;
notify pgrst, 'reload schema';
