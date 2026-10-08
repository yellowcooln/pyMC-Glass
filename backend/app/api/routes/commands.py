import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.v1.command import QueueCommandRequestV1, QueueCommandResponseV1
from app.contracts.v2.command import JobV2, QueryV2
from app.contracts.v2.common import UUIDValue
from app.db.models import CommandQueueItem, DeviceCommand, Repeater, User
from app.db.session import get_db_session
from app.schemas.commands import CommandQueueItemResponse, CommandState, DeviceCommandResponse
from app.security.deps import require_roles
from app.services.command_dispatch import (
    admit_command,
    cancel_command,
    clock,
    command_target,
    effective_status_filter,
    reconcile_commands,
)

router = APIRouter(prefix="/api/commands")


def _to_response(item: CommandQueueItem, node_name: str) -> CommandQueueItemResponse:
    return CommandQueueItemResponse(
        command_id=item.id,
        repeater_id=item.repeater_id,
        node_name=node_name,
        action=item.command,
        status=item.status,
        params=json.loads(item.params_json or "{}"),
        result=json.loads(item.result_json) if item.result_json else None,
        requested_by=item.requested_by,
        created_at=item.created_at,
        completed_at=item.completed_at,
    )


@router.post("", response_model=QueueCommandResponseV1, status_code=status.HTTP_201_CREATED)
def queue_command(
    payload: QueueCommandRequestV1,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(require_roles("admin", "operator")),
) -> QueueCommandResponseV1:
    raise HTTPException(409, "Typed v2 command admission required")


def _typed_response(item: DeviceCommand) -> DeviceCommandResponse:
    return DeviceCommandResponse(
        command_id=item.id,
        device_id=item.device_id,
        request_id=item.request_id,
        execution_id=item.execution_id,
        action=item.action,
        status=item.status,
        request=json.loads(item.request_json),
        requested_by=item.requested_by,
        requester_user_id=item.requester_user_id,
        created_at=item.created_at,
        expires_at=item.expires_at,
        lease_id=item.lease_id,
        lease_expires_at=item.lease_expires_at,
        attempt=item.attempt,
        completed_at=item.completed_at,
        result=json.loads(item.result_json) if item.result_json else None,
        acceptance_id=item.acceptance_id,
        result_sha256=item.result_sha256,
        persisted=item.persisted,
        applied=item.applied,
        restart_required=item.restart_required,
        error_code=item.error_code,
        superseded_by=item.superseded_by,
    )


@router.post("/v2", response_model=DeviceCommandResponse, status_code=201)
def queue_command_v2(
    payload: QueryV2 | JobV2,
    supersedes_command_id: UUIDValue | None = Query(default=None),
    db: Session = Depends(get_db_session),
    current_user: User = Depends(require_roles("admin", "operator", "viewer")),
) -> DeviceCommandResponse:
    item = admit_command(
        db,
        payload,
        current_user,
        supersedes_command_id=str(supersedes_command_id) if supersedes_command_id else None,
    )
    response = _typed_response(item)
    db.commit()
    return response


@router.get("/v2", response_model=list[DeviceCommandResponse])
def list_commands_v2(
    status_filter: CommandState | None = Query(default=None, alias="status"),
    device_id: UUIDValue | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=256),
    offset: int = Query(default=0, ge=0, le=10000),
    db: Session = Depends(get_db_session),
    _: User = Depends(require_roles("admin", "operator", "viewer")),
) -> list[DeviceCommandResponse]:
    now = clock(None)
    # Device-specific reads reconcile before WHERE/pagination. Global reads use
    # deadline-derived SQL selection, then lock only the selected device parents.
    if device_id:
        reconcile_commands(db, str(device_id))
    candidate = (
        select(DeviceCommand)
        .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id)
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    if device_id:
        candidate = candidate.where(DeviceCommand.device_id == str(device_id))
    if status_filter:
        candidate = candidate.where(
            DeviceCommand.status == status_filter
            if device_id
            else effective_status_filter(status_filter, now)
        )
    items = db.scalars(candidate).all()
    if not device_id:
        for parent in sorted({r.device_id for r in items}):
            # Selection time is advisory, never an injected mutation timestamp.
            reconcile_commands(db, parent)
        # Refresh exact selected IDs after parent locks, including inactive rows
        # another transaction may have resolved since initial page selection.
        if items:
            items = db.scalars(
                select(DeviceCommand)
                .where(DeviceCommand.id.in_([r.id for r in items]))
                .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id)
                .execution_options(populate_existing=True)
            ).all()
    response = [
        _typed_response(r) for r in items if status_filter is None or r.status == status_filter
    ]
    db.commit()
    return response


@router.get("/v2/{command_id}", response_model=DeviceCommandResponse)
def get_command_v2(
    command_id: UUIDValue,
    db: Session = Depends(get_db_session),
    _: User = Depends(require_roles("admin", "operator", "viewer")),
) -> DeviceCommandResponse:
    parent = db.scalar(select(DeviceCommand.device_id).where(DeviceCommand.id == str(command_id)))
    if parent is None:
        raise HTTPException(404, "Command not found")
    reconcile_commands(db, parent)
    item = command_target(db, parent, str(command_id))
    if item is None:
        raise HTTPException(404, "Command not found")
    response = _typed_response(item)
    db.commit()
    return response


@router.post("/v2/{command_id}/cancel", response_model=DeviceCommandResponse)
def cancel_command_v2(
    command_id: UUIDValue,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(require_roles("admin", "operator", "viewer")),
) -> DeviceCommandResponse:
    item = cancel_command(db, str(command_id), current_user)
    response = _typed_response(item)
    db.commit()
    return response


@router.get("", response_model=list[CommandQueueItemResponse])
def list_commands(
    status_filter: str | None = Query(default=None, alias="status"),
    node_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db_session),
    _: User = Depends(require_roles("admin", "operator", "viewer")),
) -> list[CommandQueueItemResponse]:
    query = (
        select(CommandQueueItem, Repeater.node_name)
        .join(Repeater, Repeater.id == CommandQueueItem.repeater_id)
        .order_by(CommandQueueItem.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        query = query.where(CommandQueueItem.status == status_filter)
    if node_name:
        query = query.where(Repeater.node_name == node_name)

    rows = db.execute(query).all()
    return [_to_response(item, repeater_node_name) for item, repeater_node_name in rows]


@router.get("/{command_id}", response_model=CommandQueueItemResponse)
def get_command(
    command_id: str,
    db: Session = Depends(get_db_session),
    _: User = Depends(require_roles("admin", "operator", "viewer")),
) -> CommandQueueItemResponse:
    row = db.execute(
        select(CommandQueueItem, Repeater.node_name)
        .join(Repeater, Repeater.id == CommandQueueItem.repeater_id)
        .where(CommandQueueItem.id == command_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found")

    item, repeater_node_name = row
    return _to_response(item, repeater_node_name)
