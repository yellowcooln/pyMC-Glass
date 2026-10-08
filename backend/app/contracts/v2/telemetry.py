"""Outbound telemetry and safe, deliberately non-authoritative legacy observations."""

from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.contracts.v2.command import JobV2, QueryV2, ResultAcceptanceV2, ResultV2, result_sha256
from app.contracts.v2.common import Contract, Envelope, UTCDateTime, UUIDValue, checked_json
from app.contracts.v2.device import Capabilities, InventoryV2


class InformV2(Envelope):
    type: Literal["inform"]
    boot_id: UUIDValue
    sent_at: UTCDateTime
    node_name: str = Field(min_length=1, max_length=64)
    pubkey: str | None = Field(default=None, pattern=r"^0x[0-9a-f]{64}$")
    software_version: str = Field(min_length=1, max_length=64)
    capabilities: Capabilities
    inventory: InventoryV2
    # Observation JSON is intentionally opaque: it never grants capabilities or IDs.
    telemetry: dict[str, Any]
    results: tuple[ResultV2, ...] = Field(max_length=64)

    @model_validator(mode="before")
    @classmethod
    def arrays(cls, value):
        if isinstance(value, dict) and type(value.get("results")) is list:
            return {**value, "results": tuple(value["results"])}
        return value

    @field_validator("telemetry", mode="before")
    @classmethod
    def telemetry_json(cls, value):
        return checked_json(value, json_only=True)

    @model_validator(mode="after")
    def result_binding(self):
        keys = []
        for result in self.results:
            if result.device_id != self.device_id or result.sent_at > self.sent_at:
                raise ValueError("result device/timestamp does not match inform")
            keys.append((result.request_id, result.execution_id))
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate result identity")
        # Boot may differ: a durable queued result can survive a reboot.
        return self


class ResponseV2(Envelope):
    type: Literal["response"]
    boot_id: UUIDValue
    sent_at: UTCDateTime
    interval_seconds: int = Field(ge=5, le=3600)
    accepted_results: tuple[ResultAcceptanceV2, ...] = Field(max_length=64)
    queries: tuple[QueryV2, ...] = Field(max_length=64)
    jobs: tuple[JobV2, ...] = Field(max_length=64)

    @model_validator(mode="before")
    @classmethod
    def arrays(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ("accepted_results", "queries", "jobs"):
                if type(value.get(key)) is list:
                    value[key] = tuple(value[key])
        return value

    @model_validator(mode="after")
    def identities(self):
        keys = [(r.request_id, r.execution_id) for r in self.accepted_results]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate result acceptance")
        requests = (*self.queries, *self.jobs)
        if len({r.request_id for r in requests}) != len(requests):
            raise ValueError("duplicate request identity")
        if any(r.device_id != self.device_id for r in requests):
            raise ValueError("request device mismatch")
        if len({r.execution_id for r in self.jobs}) != len(self.jobs):
            raise ValueError("duplicate execution identity")
        return self

    def check_inform(self, inform: InformV2) -> None:
        if self.device_id != inform.device_id or self.boot_id != inform.boot_id:
            raise ValueError("response identity mismatch")
        offered = {(r.request_id, r.execution_id): r for r in inform.results}
        if any((r.request_id, r.execution_id) not in offered for r in self.accepted_results):
            raise ValueError("acceptance references an unoffered result")
        for receipt in self.accepted_results:
            if receipt.result_sha256 is not None and receipt.result_sha256 != result_sha256(
                offered[(receipt.request_id, receipt.execution_id)]
            ):
                raise ValueError("acceptance digest does not match offered result")
        if self.sent_at < inform.sent_at:
            raise ValueError("response predates inform")


class LegacyObservation(Contract):
    protocol: Literal[1] = 1
    device_id: None = None
    boot_id: None = None
    capabilities: dict[str, int] = Field(default_factory=dict, max_length=0)
    control_allowed: Literal[False] = False
    observations: dict[str, Any]


def adapt_legacy_observation(raw: dict[str, Any]) -> LegacyObservation:
    """Preserve observations, including zero/null radio; do not invoke stricter v1 RF model."""
    checked_json(raw, json_only=True)
    if type(raw) is not dict or raw.get("type") != "inform":
        raise ValueError("expected legacy inform")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("expected explicit protocol1")
    return LegacyObservation(observations=deepcopy(raw))
