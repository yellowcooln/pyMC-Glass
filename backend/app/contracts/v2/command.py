"""Bounded typed command wire contracts, independent of persistence and execution."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.contracts.v2.common import (
    Contract,
    Details,
    Envelope,
    PositiveVersion,
    ResourceID,
    UTCDateTime,
    UUIDValue,
    utc_value,
    uuid_value,
)

# Parent-owned catalog: action name is the required capability name; version 1 only.
ACTION_VERSIONS = {"diagnostic.read": 1, "config.read": 1, "set_mode": 1}


class ReadParamsV2(Contract):
    """Initial reads have no parameters; extensions require a catalog change."""


class SetModeParamsV2(Contract):
    mode: Literal["forward", "monitor", "no_tx"]


class RequestV2(Envelope):
    request_id: UUIDValue
    capability_version: PositiveVersion
    created_at: UTCDateTime
    expires_at: UTCDateTime
    params: Details
    expected_revision: str | None = Field(default=None, min_length=1, max_length=64)
    lease_id: UUIDValue | None = Field(default=None, exclude_if=lambda v: v is None)
    attempt: int | None = Field(
        default=None, strict=True, ge=1, le=3, exclude_if=lambda v: v is None
    )
    lease_expires_at: UTCDateTime | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def delivery_lease(self):
        values = (self.lease_id, self.attempt, self.lease_expires_at)
        if any(v is not None for v in values):
            if any(v is None for v in values):
                raise ValueError("delivery lease fields must be present together")
            if not self.created_at < self.lease_expires_at <= self.expires_at:
                raise ValueError("delivery lease deadline outside request lifetime")
        return self

    @model_validator(mode="after")
    def lifetime_and_params(self):
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        action = self.action
        if self.capability_version != ACTION_VERSIONS[action]:
            raise ValueError("incompatible action capability version")
        if action == "set_mode":
            SetModeParamsV2.model_validate(self.params)
        else:
            ReadParamsV2.model_validate(self.params)
        return self

    def check_acceptance(
        self, *, device_id: str, capabilities: dict[str, int], now: datetime
    ) -> None:
        """Contextual check before queuing; expiry is exclusive, never wall-clock implicit."""
        now = utc_value(now)
        if uuid_value(device_id) != self.device_id:
            raise ValueError("device identity mismatch")
        version = capabilities.get(self.action)
        if type(version) is not int or version != self.capability_version:
            raise ValueError("required capability absent or incompatible")
        if not self.created_at <= now < self.expires_at:
            raise ValueError("request is not currently valid")


class QueryV2(RequestV2):
    type: Literal["query"]
    action: Literal["diagnostic.read", "config.read"]


class JobV2(RequestV2):
    type: Literal["job"]
    action: Literal["set_mode"]
    execution_id: UUIDValue
    idempotency_key: ResourceID

    def check_replay(self, prior: "JobV2") -> bool:
        """Compare an already stored job; this helper is not durable deduplication."""
        if (self.device_id, self.idempotency_key) != (prior.device_id, prior.idempotency_key):
            return False
        if self.model_dump(mode="json") != prior.model_dump(mode="json"):
            raise ValueError("idempotency key reused with a different job")
        return True


class ResultV2(Envelope):
    type: Literal["result"]
    boot_id: UUIDValue
    sent_at: UTCDateTime
    request_id: UUIDValue
    # Query outcomes have null execution_id; jobs must match a non-null execution UUID.
    execution_id: UUIDValue | None
    status: Literal[
        "accepted",
        "received",
        "running",
        "awaiting_verification",
        "succeeded",
        "failed",
        "unsupported",
        "conflict",
        "unknown",
    ]
    persisted: bool | None = None
    applied: bool | None = None
    restart_required: bool | None = None
    error_code: ResourceID | None = None
    message: str | None = Field(default=None, max_length=1024)
    details: Details = Field(default_factory=dict)
    completed_at: UTCDateTime | None = None
    lease_id: UUIDValue | None = Field(default=None, exclude_if=lambda v: v is None)
    attempt: int | None = Field(
        default=None, strict=True, ge=1, le=3, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def completion(self):
        if (self.lease_id is None) != (self.attempt is None):
            raise ValueError("result lease and attempt must be present together")
        terminal = self.status in {"succeeded", "failed", "unsupported", "conflict"}
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal outcomes require completion; nonterminal outcomes forbid it")
        if self.completed_at is not None and self.completed_at > self.sent_at:
            raise ValueError("completion cannot be after sent_at")
        return self

    def check_request(self, request: QueryV2 | JobV2) -> None:
        if (
            self.device_id != request.device_id
            or self.request_id != request.request_id
            or self.execution_id != getattr(request, "execution_id", None)
        ):
            raise ValueError("result does not match request identity")
        if self.sent_at < request.created_at or (
            self.completed_at is not None and self.completed_at < request.created_at
        ):
            raise ValueError("result predates request")
        # Late outcomes remain observable; request expiry is checked before execution,
        # not used to discard a completed outcome after a transport outage.


class ResultAcceptanceV2(Contract):
    request_id: UUIDValue
    execution_id: UUIDValue | None
    acceptance_id: UUIDValue | None = Field(default=None, exclude_if=lambda v: v is None)
    result_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def receipt_pair(self):
        if (self.acceptance_id is None) != (self.result_sha256 is None):
            raise ValueError("acceptance ID and digest must be present together")
        return self


def canonical_json(model: Contract) -> str:
    """Canonical UTF-8 JSON for durable request/result equality and receipt binding."""
    import json

    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def result_sha256(result: ResultV2) -> str:
    from hashlib import sha256

    return sha256(canonical_json(result).encode("utf-8")).hexdigest()
