"""Atomic authenticated observation, durable result acceptance and command leasing."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.v2.command import JobV2, QueryV2
from app.contracts.v2.common import MAX_ENVELOPE_BYTES, decode_json
from app.contracts.v2.telemetry import InformV2, ResponseV2
from app.db.models import DeviceObservation, Repeater
from app.db.session import get_db_session
from app.security.devices import bind_device_identity, get_current_device, lock_repeater
from app.services.command_dispatch import accept_result, claim_commands

router = APIRouter()


async def bounded_inform_json(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_ENVELOPE_BYTES:
            raise HTTPException(413, "Inform body too large")
        body.extend(chunk)
    try:
        return decode_json(bytes(body))
    except ValueError as exc:
        raise HTTPException(422, "Invalid inform envelope") from exc


def check_name_collision(db: Session, device: Repeater, node_name: str) -> None:
    other = db.scalar(select(Repeater.id).where(Repeater.node_name == node_name))
    if other is not None and other != device.id:
        raise HTTPException(409, "Node name collision")


@router.post("/inform/v2", response_model=ResponseV2)
def inform_v2(
    raw: dict = Depends(bounded_inform_json),
    device: Repeater = Depends(get_current_device),
    db: Session = Depends(get_db_session),
) -> ResponseV2:
    try:
        payload = InformV2.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(422, "Invalid inform envelope") from exc
    device = lock_repeater(db, device.id)
    if device is None:
        raise HTTPException(401, "Invalid device credential")
    bind_device_identity(device, device_id=str(payload.device_id), pubkey=payload.pubkey)
    check_name_collision(db, device, payload.node_name)
    row = db.get(DeviceObservation, device.id)
    if row is None:
        row = DeviceObservation(repeater_id=device.id)
        db.add(row)
    row.boot_id = str(payload.boot_id)
    row.sent_at = payload.sent_at
    now = datetime.now(UTC)
    row.received_at = now
    wire = payload.model_dump(mode="json")
    row.capabilities_json = json.dumps(wire["capabilities"], separators=(",", ":"), sort_keys=True)
    row.inventory_json = json.dumps(wire["inventory"], separators=(",", ":"), sort_keys=True)
    row.telemetry_json = json.dumps(wire["telemetry"], separators=(",", ":"), sort_keys=True)
    try:
        db.flush()
        receipts = tuple(
            accept_result(db, device.id, result, now=now) for result in payload.results
        )
        deliveries = claim_commands(db, device.id, payload.capabilities, now=now)
        response = ResponseV2(
            version=2,
            type="response",
            device_id=payload.device_id,
            boot_id=payload.boot_id,
            sent_at=max(datetime.now(UTC), payload.sent_at),
            interval_seconds=30,
            accepted_results=receipts,
            queries=tuple(r for r in deliveries if isinstance(r, QueryV2)),
            jobs=tuple(r for r in deliveries if isinstance(r, JobV2)),
        )
        response.check_inform(payload)
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise
