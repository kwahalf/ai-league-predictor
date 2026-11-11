from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from temporalio import activity
from temporalio.exceptions import ApplicationError
from .client import operation_id, request


@dataclass
class EntityResult:
    entity_id: str
    version_id: str
    success: bool = True
    error: str | None = None


def uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApplicationError('A valid UUID is required', non_retryable=True) from exc


@activity.defn
def create_entity(entity_type: str, attributes: dict[str, Any], created_by: str | None = None) -> EntityResult:
    result = request('rpc/create_entity_with_version', {
        'p_entity_type': entity_type, 'p_data': attributes, 'p_entity_id': operation_id('entity'),
    })
    return EntityResult(**result)


@activity.defn
def update_entity_scd2(entity_id: str, attributes: dict[str, Any], updated_by: str | None = None) -> EntityResult:
    result = request('rpc/update_entity_with_version', {
        'p_entity_id': uuid(entity_id), 'p_data': attributes, 'p_version_id': operation_id('version'),
    })
    return EntityResult(**result)


@activity.defn
def get_entity(entity_id: str) -> dict[str, Any]:
    rows = request(f'entities?id=eq.{uuid(entity_id)}&select=*,entity_versions(*)&entity_versions.is_current=eq.true', method='GET')
    if not rows:
        raise ApplicationError('Entity not found', non_retryable=True)
    return rows[0]


@activity.defn
def append_event(entity_id: str, entity_type: str, event_type: str, event_data: dict[str, Any], actor_id: str | None = None, correlation_id: str | None = None) -> bool:
    return request('rpc/append_entity_event', {
        'p_entity_id': uuid(entity_id), 'p_entity_type': entity_type, 'p_event_type': event_type,
        'p_data': event_data, 'p_actor_id': actor_id, 'p_correlation_id': correlation_id,
        'p_event_id': operation_id('event'),
    })


@activity.defn
def create_relationship(from_entity_id: str, to_entity_id: str, relationship_type: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    return request('rpc/create_entity_relationship', {
        'p_parent_id': uuid(from_entity_id), 'p_child_id': uuid(to_entity_id),
        'p_relationship_type': relationship_type, 'p_metadata': attributes or {},
        'p_relationship_id': operation_id('relationship'),
    })
