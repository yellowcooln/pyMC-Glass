"""Bounded managed-MQTT identity parsing; deliberately not wired into legacy ingest.

Parsing proves topic/envelope consistency, NOT certificate ownership or DB eligibility.
The future caller must resolve device_id and enforce active, non-revoked credentials.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

MAX_MESSAGE_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
_RESOURCE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_REQUIRED_FIELDS = {"version", "type", "device_id", "topic", "node_name", "timestamp", "payload"}


def canonical_device_id(value: str) -> str:
    """Reject noncanonical UUIDs; never silently repair authority identifiers."""
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("device_id must be a canonical UUID string")
    try:
        valid = str(UUID(value)) == value
    except ValueError as exc:
        raise ValueError("device_id must be a canonical UUID string") from exc
    if not valid:
        raise ValueError("device_id must be a canonical UUID string")
    return value


@dataclass(frozen=True, slots=True)
class ManagedMqttMessage:
    device_id: str
    topic: str
    node_name: str  # Display only; never an authentication or lookup key.
    timestamp: datetime
    event_type: str
    event_name: str | None
    payload: dict[str, Any]
    canonical_payload: str


def _topic_identity(topic: str) -> tuple[str, str, str | None]:
    if not isinstance(topic, str) or len(topic) > 128:
        raise ValueError("invalid managed topic")
    parts = topic.split("/")
    if len(parts) not in (3, 4) or parts[0] != "glass" or not parts[1].startswith("device:"):
        raise ValueError("invalid managed topic")
    device_id = canonical_device_id(parts[1][7:])
    event_type = parts[2]
    if len(parts) == 3 and event_type in ("packet", "advert"):
        return device_id, event_type, None
    if len(parts) == 4 and event_type == "event" and _RESOURCE_ID.fullmatch(parts[3]):
        return device_id, event_type, parts[3]
    raise ValueError("invalid managed topic record")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _bounded_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("missing or oversized MQTT envelope")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid MQTT JSON") from exc
    # Count root, values and object keys, with root at depth 1. No recursion here.
    stack = [(value, 1)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > MAX_JSON_DEPTH or count > MAX_JSON_NODES:
            raise ValueError("MQTT JSON complexity limit exceeded")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite JSON number")
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("invalid Unicode scalar") from exc
    if not isinstance(value, dict):
        raise ValueError("MQTT envelope must be an object")
    return value


def _timestamp(value: Any) -> datetime:
    try:
        if type(value) in (int, float):
            result = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value)
            if result.tzinfo is None or result.utcoffset() is None:
                raise ValueError("timestamp must include timezone")
            result = result.astimezone(UTC)
        else:
            raise ValueError("timestamp must be explicit")
        if result < datetime(1970, 1, 1, tzinfo=UTC):
            raise ValueError("timestamp before Unix epoch")
        return result
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("invalid MQTT timestamp") from exc


def parse_managed_message(topic: str, payload_bytes: bytes) -> ManagedMqttMessage:
    """Strict v2 envelope, with exact fixed-prefix topic and stable owner agreement.

    Raises ValueError for unavailable/malformed input; has no legacy fallback, DB,
    filesystem, clock, broker or other operational side effects.
    """
    device_id, event_type, event_name = _topic_identity(topic)
    raw = _bounded_json(payload_bytes)
    if not _REQUIRED_FIELDS.issubset(raw) or raw.keys() - (_REQUIRED_FIELDS | {"event_name"}):
        raise ValueError("missing or unknown envelope fields")
    if type(raw["version"]) is not int or raw["version"] != 2:
        raise ValueError("managed MQTT requires version 2")
    if canonical_device_id(raw["device_id"]) != device_id or raw["topic"] != topic:
        raise ValueError("MQTT owner/topic mismatch")
    if raw["type"] != event_type:
        raise ValueError("MQTT record type mismatch")
    if event_name is not None:
        if raw.get("event_name") != event_name:
            raise ValueError("MQTT event name mismatch")
    elif "event_name" in raw:
        raise ValueError("event_name is only valid for events")
    if not isinstance(raw["node_name"], str) or not raw["node_name"].strip():
        raise ValueError("node_name must be a nonempty display string")
    if not isinstance(raw["payload"], dict):
        raise ValueError("MQTT payload must be an object")
    return ManagedMqttMessage(
        device_id=device_id,
        topic=topic,
        node_name=raw["node_name"],
        timestamp=_timestamp(raw["timestamp"]),
        event_type=event_type,
        event_name=event_name,
        payload=raw["payload"],
        canonical_payload=json.dumps(
            raw["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )
