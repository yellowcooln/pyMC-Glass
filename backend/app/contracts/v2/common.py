"""Bounded, fail-closed wire primitives; no service or database imports."""

import json
import math
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

MAX_ENVELOPE_BYTES = 256 * 1024
MAX_DETAIL_BYTES = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 4096


def checked_json(
    value: Any, *, byte_limit: int = MAX_ENVELOPE_BYTES, json_only: bool = False
) -> Any:
    """Bound work before encoding, including arbitrary JSON embedded in telemetry."""
    nodes = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("JSON depth/node budget exceeded")
        if isinstance(item, BaseModel) and not json_only:
            item = item.model_dump(mode="json")
        if isinstance(item, (UUID, datetime)) and not json_only:
            return str(item) if isinstance(item, UUID) else item.isoformat()
        if item is None or type(item) in (bool, int, str):
            if isinstance(item, str) and len(item) > byte_limit:
                raise ValueError("JSON byte budget exceeded")
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if type(item) is list or (type(item) is tuple and not json_only):
            return [visit(child, depth + 1) for child in item]
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                raise ValueError("JSON object keys must be strings")
            return {key: visit(child, depth + 1) for key, child in item.items()}
        raise ValueError("not a finite JSON value")

    normalized = visit(value, 0)
    try:
        size = len(
            json.dumps(
                normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        )
    except (ValueError, OverflowError, UnicodeError) as exc:
        raise ValueError("invalid JSON encoding") from exc
    if size > byte_limit:
        raise ValueError("JSON byte budget exceeded")
    return value


def uuid_value(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    if type(value) is not str:
        raise ValueError("UUID must be a canonical string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("invalid UUID") from exc
    if str(parsed) != value:
        raise ValueError("UUID must use lowercase canonical hyphenated form")
    return parsed


def utc_value(value: Any) -> datetime:
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid UTC timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be UTC aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must have UTC offset zero")
    return value


def details_value(value: Any) -> Any:
    if type(value) is not dict:
        raise ValueError("details/params must be an object")
    return checked_json(value, byte_limit=MAX_DETAIL_BYTES, json_only=True)


UUIDValue = Annotated[UUID, BeforeValidator(uuid_value)]
UTCDateTime = Annotated[datetime, BeforeValidator(utc_value)]
ResourceID = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
PositiveVersion = Annotated[int, Field(strict=True, ge=1, le=65535)]
Details = Annotated[dict[str, Any], BeforeValidator(details_value)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def bound_json(cls, value: Any) -> Any:
        return checked_json(value)

    @model_validator(mode="after")
    def bound_serialized_json(self):
        checked_json(self.model_dump(mode="json"))
        return self


class Envelope(Contract):
    version: Literal[2]
    device_id: UUIDValue

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if isinstance(value, dict) and type(value.get("version")) is not int:
            raise ValueError("version must be an integer")
        return value


def decode_json(payload: str | bytes) -> dict[str, Any]:
    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ValueError("JSON byte budget exceeded")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError(f"nonfinite JSON constant: {value}")

        raw = json.loads(encoded, object_pairs_hook=pairs, parse_constant=reject_constant)
        if type(raw) is not dict:
            raise ValueError("envelope must be an object")
        return checked_json(raw)
    except (RecursionError, UnicodeError, TypeError) as exc:
        raise ValueError("invalid JSON envelope") from exc
