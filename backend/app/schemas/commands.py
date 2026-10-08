from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class CommandQueueItemResponse(BaseModel):
    command_id: str
    repeater_id: str
    node_name: str
    action: str
    status: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    requested_by: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


CommandState = Literal[
    "queued",
    "received",
    "running",
    "awaiting_verification",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
    "unknown",
]


class DeviceCommandResponse(BaseModel):
    command_id: str
    device_id: str
    request_id: str
    execution_id: str | None
    action: str
    status: CommandState
    request: dict[str, Any]
    requested_by: str
    requester_user_id: str
    created_at: datetime
    expires_at: datetime
    lease_id: str | None
    lease_expires_at: datetime | None
    attempt: int
    completed_at: datetime | None
    result: dict[str, Any] | None
    acceptance_id: str | None
    result_sha256: str | None
    persisted: bool | None
    applied: bool | None
    restart_required: bool | None
    error_code: str | None
    superseded_by: str | None
