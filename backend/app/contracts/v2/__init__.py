"""Canonical, offline protocol2 library. Does not register routes or activate control."""

from app.contracts.v2.command import JobV2, QueryV2, ResultAcceptanceV2, ResultV2
from app.contracts.v2.common import decode_json
from app.contracts.v2.device import DeviceIdentityV2, IdentityV2, InventoryV2, negotiate_protocol
from app.contracts.v2.telemetry import (
    InformV2,
    LegacyObservation,
    ResponseV2,
    adapt_legacy_observation,
)

__all__ = [
    "DeviceIdentityV2",
    "IdentityV2",
    "InformV2",
    "InventoryV2",
    "JobV2",
    "LegacyObservation",
    "QueryV2",
    "ResponseV2",
    "ResultAcceptanceV2",
    "ResultV2",
    "adapt_legacy_observation",
    "negotiate_protocol",
    "parse_envelope",
]


def parse_envelope(payload: str | bytes):
    raw = decode_json(payload)
    models = {
        "inform": InformV2,
        "query": QueryV2,
        "job": JobV2,
        "result": ResultV2,
        "response": ResponseV2,
    }
    try:
        model = models[raw.get("type")]
    except (KeyError, TypeError) as exc:
        raise ValueError("unknown envelope type") from exc
    return model.model_validate(raw)
