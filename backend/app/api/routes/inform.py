import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.inform_v2 import bounded_inform_json, check_name_collision
from app.config import get_settings
from app.contracts.v1.inform import InformRequestV1
from app.db.models import CommandQueueItem, InformSnapshot, Repeater
from app.db.session import get_db_session
from app.security.devices import bind_device_identity, get_current_device, lock_repeater
from app.services.alert_policy import evaluate_policies_for_repeater
from app.services.audit import write_audit_log
from app.services.config_snapshot import (
    ConfigSnapshotService,
    SnapshotEncryptionError,
    SnapshotPayloadError,
)
from app.services.repeater_policy import (
    mark_repeater_policy_sync_dispatched,
    mark_repeater_policy_sync_result,
)
from app.services.system_settings import (
    get_effective_config_snapshot_encryption_keys,
    get_effective_managed_mqtt_settings,
)
from app.services.transport_keys import (
    mark_transport_key_sync_dispatched,
    mark_transport_key_sync_result,
)

router = APIRouter()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_docker_bridge_gateway(host: str | None) -> bool:
    if not host:
        return False
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    return octets[0] == 172 and 16 <= octets[1] <= 31 and octets[2:] == [0, 1]


def _inform_source_ip(request: Request, current_ip: str | None = None) -> str | None:
    source_ip = request.client.host if request.client else None
    if (
        _is_docker_bridge_gateway(source_ip)
        and current_ip
        and not _is_docker_bridge_gateway(current_ip)
    ):
        return current_ip
    return source_ip or current_ip


def _compact_json(value: dict[str, Any]) -> str | None:
    if not value:
        return None
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _normalize_location(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized_text = text.replace(";", ",")
    parts = [part.strip() for part in normalized_text.split(",") if part.strip()]
    lat: float | None = None
    lng: float | None = None

    if len(parts) >= 2:
        try:
            lat = float(parts[0])
            lng = float(parts[1])
        except ValueError:
            lat = None
            lng = None

    if lat is None or lng is None:
        numbers = re.findall(r"[-+]?\d*\.?\d+", normalized_text)
        if len(numbers) >= 2:
            try:
                lat = float(numbers[0])
                lng = float(numbers[1])
            except ValueError:
                return None
        else:
            return None

    if lat is None or lng is None:
        return None
    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return None
    return f"{lat:.6f},{lng:.6f}"


def _extract_location_from_settings(settings: dict[str, Any]) -> str | None:
    if not settings:
        return None
    repeater_settings = settings.get("repeater")
    repeater_dict = repeater_settings if isinstance(repeater_settings, dict) else {}
    candidates = [
        settings.get("location"),
        repeater_dict.get("location"),
        settings.get("gps"),
        repeater_dict.get("gps"),
        {
            "lat": repeater_dict.get("lat", repeater_dict.get("latitude")),
            "lng": repeater_dict.get(
                "lng",
                repeater_dict.get("lon", repeater_dict.get("longitude")),
            ),
        },
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = _normalize_location(candidate)
            if normalized:
                return normalized
        if isinstance(candidate, dict):
            lat = candidate.get("lat", candidate.get("latitude"))
            lng = candidate.get("lng", candidate.get("lon", candidate.get("longitude")))
            try:
                if lat is None or lng is None:
                    continue
                normalized = _normalize_location(f"{float(lat)},{float(lng)}")
                if normalized:
                    return normalized
            except (TypeError, ValueError):
                continue
    top_lat = settings.get("latitude")
    top_lng = settings.get("lon", settings.get("longitude"))
    if top_lat is not None and top_lng is not None:
        normalized = _normalize_location(f"{top_lat},{top_lng}")
        if normalized:
            return normalized
    return None


def _build_glass_managed_mqtt_payload(db: Session) -> dict:
    managed, _, _ = get_effective_managed_mqtt_settings(db)
    return managed


def _ensure_glass_managed_mqtt_command(db: Session, repeater: Repeater) -> None:
    existing = db.scalar(
        select(CommandQueueItem.id).where(
            CommandQueueItem.repeater_id == repeater.id,
            CommandQueueItem.command == "config_update",
            CommandQueueItem.requested_by == "system:glass-managed-mqtt",
        )
    )
    if existing is not None:
        return

    params = {
        "config": {
            "glass_managed": _build_glass_managed_mqtt_payload(db),
        },
        "merge_mode": "patch",
    }
    db.add(
        CommandQueueItem(
            repeater_id=repeater.id,
            command="config_update",
            params_json=json.dumps(params),
            status="queued",
            requested_by="system:glass-managed-mqtt",
        )
    )
    write_audit_log(
        db,
        action="system_glass_mqtt_config_queued",
        target_type="repeater",
        target_id=repeater.id,
        details={"node_name": repeater.node_name, "source": "inform"},
    )


@router.post("/inform")
def inform(
    request: Request,
    raw: dict = Depends(bounded_inform_json),
    db: Session = Depends(get_db_session),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> dict:
    try:
        payload = InformRequestV1.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(422, "Invalid inform envelope") from exc
    if request.headers.get("authorization") is not None:
        repeater = get_current_device(request, credentials, db)
        bind_device_identity(repeater, device_id=repeater.id, pubkey=payload.pubkey)
        check_name_collision(db, repeater, payload.node_name)
    else:
        by_name = db.scalar(select(Repeater).where(Repeater.node_name == payload.node_name))
        by_key = db.scalar(select(Repeater).where(Repeater.pubkey == payload.pubkey))
        # Discovery must not race adoption/rejection into an anonymous operational write.
        existing_ids = sorted({row.id for row in (by_name, by_key) if row is not None})
        locked = {device_id: lock_repeater(db, device_id) for device_id in existing_ids}
        if any(row is None for row in locked.values()):
            raise HTTPException(401, "Discovery identity changed")
        by_name = locked[by_name.id] if by_name is not None else None
        by_key = locked[by_key.id] if by_key is not None else None
        if any(row is not None and row.status != "pending_adoption" for row in (by_name, by_key)):
            # No observation, result ingestion, provisioning or dispatch before authority.
            get_current_device(request, credentials, db)
        if payload.command_results:
            raise HTTPException(401, "Device credential required for results")
        if (by_name is not None or by_key is not None) and (
            by_name is None or by_key is None or by_name.id != by_key.id
        ):
            raise HTTPException(409, "Discovery identity collision")
        repeater = by_name
    now = _utc_now()
    settings = get_settings()
    effective_snapshot_keys, _, _ = get_effective_config_snapshot_encryption_keys(db)
    config_snapshot_service = ConfigSnapshotService(
        settings,
        encryption_keys=effective_snapshot_keys,
    )

    system_payload = payload.system.model_dump()
    if payload.sensors is not None:
        system_payload["sensors"] = payload.sensors

    if repeater is None:
        location = _normalize_location(payload.location) or _extract_location_from_settings(
            payload.settings
        )

    if repeater is None:
        repeater = Repeater(
            node_name=payload.node_name,
            pubkey=payload.pubkey,
            status="pending_adoption",
            firmware_version=payload.software_version,
            state=payload.state,
            location=location,
            config_hash=payload.config_hash,
            inform_ip=_inform_source_ip(request),
            last_inform_at=now,
            system_json=_compact_json(system_payload),
            radio_json=_compact_json(payload.radio.model_dump()),
            counters_json=_compact_json(payload.counters.model_dump()),
            settings_json=_compact_json(payload.settings),
        )
        db.add(repeater)
        db.flush()
        write_audit_log(
            db,
            action="repeater_pending_discovered",
            target_type="repeater",
            target_id=repeater.id,
            details={"node_name": payload.node_name, "pubkey": payload.pubkey},
        )
    else:
        location = _normalize_location(payload.location) or _extract_location_from_settings(
            payload.settings
        )
        repeater.firmware_version = payload.software_version
        repeater.state = payload.state
        if location is not None:
            repeater.location = location
        repeater.config_hash = payload.config_hash
        repeater.inform_ip = _inform_source_ip(request, repeater.inform_ip)
        repeater.last_inform_at = now
        repeater.system_json = _compact_json(system_payload)
        repeater.radio_json = _compact_json(payload.radio.model_dump())
        repeater.counters_json = _compact_json(payload.counters.model_dump())
        if payload.settings:
            repeater.settings_json = _compact_json(payload.settings)
        if repeater.status in {"adopted", "connected", "offline"}:
            repeater.status = "connected"

    db.add(
        InformSnapshot(
            repeater_id=repeater.id,
            timestamp=now,
            cpu=payload.system.cpu_percent,
            memory=payload.system.memory_percent,
            disk=payload.system.disk_percent,
            uptime_seconds=payload.uptime_seconds,
            noise_floor=payload.radio.noise_floor_dbm,
            rx_total=payload.counters.rx_total,
            tx_total=payload.counters.tx_total,
            forwarded=payload.counters.forwarded,
            dropped=payload.counters.dropped,
            airtime_percent=payload.counters.airtime_percent,
        )
    )

    for result in payload.command_results:
        queued = db.scalar(
            select(CommandQueueItem).where(
                CommandQueueItem.id == result.command_id,
                CommandQueueItem.repeater_id == repeater.id,
            )
        )
        if queued is None:
            continue
        queued.status = result.status
        queued.completed_at = result.completed_at
        result_payload: dict[str, Any] = {
            "message": result.message,
            "completed_at": result.completed_at.isoformat(),
        }
        result_details = result.details if isinstance(result.details, dict) else {}
        if queued.command == "export_config":
            if result.status == "success":
                try:
                    export_payload = config_snapshot_service.extract_export_payload(result_details)
                    snapshot_result = config_snapshot_service.store_snapshot(
                        db,
                        repeater_id=repeater.id,
                        command_id=queued.id,
                        captured_at=_normalize_datetime(result.completed_at) or now,
                        payload=export_payload,
                    )
                    result_payload["details"] = {
                        "snapshot_id": snapshot_result.snapshot.id,
                        "payload_sha256": snapshot_result.snapshot.payload_sha256,
                        "payload_size_bytes": snapshot_result.snapshot.payload_size_bytes,
                        "pruned_count": snapshot_result.pruned_count,
                        "stored_new_snapshot": snapshot_result.stored_new_snapshot,
                        "change_control": snapshot_result.change_control,
                    }
                    if snapshot_result.stored_new_snapshot:
                        write_audit_log(
                            db,
                            action="config_snapshot_stored",
                            target_type="config_snapshot",
                            target_id=snapshot_result.snapshot.id,
                            details={
                                "node_name": repeater.node_name,
                                "repeater_id": repeater.id,
                                "command_id": queued.id,
                                "pruned_count": snapshot_result.pruned_count,
                                "change_control": snapshot_result.change_control,
                            },
                        )
                    else:
                        write_audit_log(
                            db,
                            action="config_snapshot_unchanged",
                            target_type="config_snapshot",
                            target_id=snapshot_result.snapshot.id,
                            details={
                                "node_name": repeater.node_name,
                                "repeater_id": repeater.id,
                                "command_id": queued.id,
                                "change_control": snapshot_result.change_control,
                            },
                        )
                except (SnapshotPayloadError, SnapshotEncryptionError) as exc:
                    result_payload["details"] = {"snapshot_error": str(exc)}
                    write_audit_log(
                        db,
                        action="config_snapshot_store_failed",
                        target_type="command",
                        target_id=queued.id,
                        details={
                            "node_name": repeater.node_name,
                            "repeater_id": repeater.id,
                            "error": str(exc),
                        },
                    )
        elif result_details:
            result_payload["details"] = result_details
        queued.result_json = json.dumps(result_payload, default=str)

        if queued.command == "transport_keys_sync":
            mark_transport_key_sync_result(
                db,
                repeater_id=repeater.id,
                command_id=queued.id,
                status=result.status,
                message=result.message,
                completed_at=result.completed_at,
            )
        if queued.command == "policy_sync":
            mark_repeater_policy_sync_result(
                db,
                repeater_id=repeater.id,
                command_id=queued.id,
                status=result.status,
                message=result.message,
                completed_at=result.completed_at,
            )
        write_audit_log(
            db,
            action="command_result_ingested",
            target_type="command",
            target_id=queued.id,
            details={"status": result.status, "node_name": repeater.node_name},
        )

    if repeater.status in {"adopted", "connected", "offline"}:
        evaluate_policies_for_repeater(db, repeater, now=now)

    if repeater.status == "pending_adoption":
        db.commit()
        return {"type": "noop", "interval": 30, "status": "pending_adoption"}

    if repeater.status == "rejected":
        db.commit()
        return {"type": "noop", "interval": 300, "status": "rejected"}
    if repeater.status in {"adopted", "connected"}:
        _ensure_glass_managed_mqtt_command(db, repeater)

    # Session autoflush is disabled: persist newly queued commands and result
    # status transitions before selecting the next queued item.
    db.flush()
    next_command = db.scalar(
        select(CommandQueueItem)
        .where(
            CommandQueueItem.repeater_id == repeater.id,
            CommandQueueItem.status == "queued",
        )
        .order_by(CommandQueueItem.created_at.asc())
    )
    if next_command is not None:
        next_command.status = "dispatched"
        if next_command.command == "transport_keys_sync":
            mark_transport_key_sync_dispatched(
                db,
                repeater_id=repeater.id,
                command_id=next_command.id,
            )
        if next_command.command == "policy_sync":
            mark_repeater_policy_sync_dispatched(
                db,
                repeater_id=repeater.id,
                command_id=next_command.id,
            )
        write_audit_log(
            db,
            action="command_dispatched",
            target_type="command",
            target_id=next_command.id,
            details={"node_name": repeater.node_name, "action": next_command.command},
        )
        db.commit()
        return {
            "type": "command",
            "command_id": next_command.id,
            "action": next_command.command,
            "params": json.loads(next_command.params_json or "{}"),
        }

    db.commit()
    return {"type": "noop", "interval": 30, "status": "connected"}
