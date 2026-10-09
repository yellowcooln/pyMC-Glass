"""Durable typed admission and leasing. Caller owns the atomic transaction.

All mutations acquire Repeater -> DeviceCommand -> lease -> receipts. PostgreSQL parent
row locks serialize admission, cancellation and concurrent informs. No legacy
queue reads, execution handlers, or RF retries live here.
"""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.contracts.v2.command import JobV2, QueryV2, ResultAcceptanceV2, ResultV2, canonical_json
from app.contracts.v2.common import utc_value, uuid_value
from app.db.models import (
    Certificate,
    DeviceCommand,
    DeviceCommandLease,
    DeviceCommandReceipt,
    DeviceCredential,
    DeviceObservation,
    User,
)
from app.security.devices import ELIGIBLE_STATUSES, lock_repeater
from app.services.audit import write_audit_log

ACTIVE = {"queued", "received", "running", "awaiting_verification"}
TERMINAL = {"succeeded", "failed", "expired", "cancelled"}
PROGRESS = {"queued": 0, "received": 1, "running": 2, "awaiting_verification": 3}
MAX_RECEIPTS = 8
MAX_ACTIVE = 256
LEASE_SECONDS = 60


def aware(value):
    # SQLite drops timezone metadata, PostgreSQL preserves it. Wire never permits naive time.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def clock(now):
    return utc_value(datetime.now(UTC) if now is None else now)


def authorize(user: User, action: str):
    roles = (
        {"admin", "operator", "viewer"}
        if action in {"diagnostic.read", "config.read"}
        else {"admin", "operator"}
    )
    if not user.is_active or user.role not in roles:
        raise HTTPException(403, "Command permission denied")


def authority(db, device, now):
    if device is None:
        raise HTTPException(404, "Device not found")
    try:
        uuid_value(device.id)
    except ValueError as exc:
        raise HTTPException(409, "Device has no stable v2 identity") from exc
    credential = db.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.repeater_id == device.id)
        .execution_options(populate_existing=True)
    )
    cert = db.scalar(
        select(Certificate)
        .where(Certificate.repeater_id == device.id, Certificate.serial == device.cert_serial)
        .execution_options(populate_existing=True)
    )
    if (
        device.status not in ELIGIBLE_STATUSES
        or credential is None
        or credential.revoked_at is not None
        or credential.cert_serial != device.cert_serial
        or cert is None
        or cert.revoked_at is not None
        or aware(cert.expires_at) <= now
    ):
        raise HTTPException(409, "Current enrolled device authority required")
    return credential


def observation_caps(db, device_id, now):
    observation = db.get(DeviceObservation, device_id, populate_existing=True)
    if (
        observation is None
        or observation.received_at is None
        or not now - timedelta(seconds=120) <= aware(observation.received_at) <= now
    ):
        raise HTTPException(409, "Fresh server-received capabilities required")
    return json.loads(observation.capabilities_json)


def stored_request(item):
    model = JobV2 if item.execution_id is not None else QueryV2
    return model.model_validate(json.loads(item.request_json))


def command_rows(db, device_id):
    """Bound active reconciliation; callers hold the device parent lock.

    The extra row is a corruption sentinel, not a truncated work queue. Historical
    targets must be looked up separately so terminal history cannot strand work.
    """
    rows = db.scalars(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == device_id, DeviceCommand.status.in_(ACTIVE))
        .order_by(DeviceCommand.created_at, DeviceCommand.id)
        .limit(MAX_ACTIVE + 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) > MAX_ACTIVE:
        raise HTTPException(409, "Device active command invariant exceeded")
    return rows


def command_target(db, device_id, command_id):
    """Exact historical target lookup, only after taking its parent lock."""
    return db.scalar(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == device_id, DeviceCommand.id == command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def effective_status(now):
    """SQL counterpart of reconciliation, evaluated before global pagination."""
    active = DeviceCommand.status.in_(ACTIVE)
    lease_elapsed = and_(
        active, DeviceCommand.lease_id.is_not(None), DeviceCommand.lease_expires_at <= now
    )
    return case(
        (
            and_(active, DeviceCommand.lease_id.is_(None), DeviceCommand.expires_at <= now),
            "expired",
        ),
        (
            and_(
                lease_elapsed,
                DeviceCommand.execution_id.is_(None),
                DeviceCommand.attempt < 3,
                DeviceCommand.expires_at > now,
            ),
            "queued",
        ),
        (lease_elapsed, "unknown"),
        else_=DeviceCommand.status,
    )


def effective_status_filter(status, now):
    # Expose the existing status index to the planner instead of applying CASE
    # alone to every historic row. Only active rows can change by deadline.
    candidates = ACTIVE | {status} if status in {"expired", "unknown", "queued"} else {status}
    return and_(DeviceCommand.status.in_(candidates), effective_status(now) == status)


def audit(db, item, action, user_id=None):
    write_audit_log(
        db,
        action=action,
        target_type="device_command",
        target_id=item.id,
        user_id=user_id,
        details={"status": item.status, "attempt": item.attempt},
    )


def reconcile_commands(db, device_id, *, now=None):
    """Lazy deadline reconciliation on polls and status/API reads, under parent lock."""
    if lock_repeater(db, device_id) is None:
        raise HTTPException(404, "Device not found")
    return _reconcile_locked(db, device_id, clock(now))


def _reconcile_locked(db, device_id, now):
    """Caller holds parent lock; now is the admitted transaction timestamp."""
    rows = command_rows(db, device_id)
    for item in rows:
        before = item.status
        previous_lease = item.lease_id
        if item.status not in ACTIVE:
            continue
        if item.lease_id is None:
            if aware(item.expires_at) <= now:
                item.status = "expired"
                item.completed_at = now
        elif aware(item.lease_expires_at) <= now:
            # set_mode, including no_tx and monitor, is never blindly re-executed.
            if item.execution_id is None and item.attempt < 3 and now < aware(item.expires_at):
                item.status = "queued"
                # Keep the original offer until an actual reclaim replaces it.
                # A status read must not discard a late original-lease outcome.
            else:
                item.status = "unknown"
                item.completed_at = None
        if before != item.status or previous_lease != item.lease_id:
            audit(db, item, "command_deadline_reconciled")
    db.flush()
    return rows


def admit_command(
    db: Session, request: QueryV2 | JobV2, user: User, *, now=None, supersedes_command_id=None
):
    authorize(user, request.action)
    if request.lease_id is not None:
        raise HTTPException(422, "Delivery fields are server-owned")
    device = lock_repeater(db, str(request.device_id))
    # Production time must be sampled after a potentially blocking parent lock.
    # Explicit test timestamps retain their deterministic meaning.
    now = clock(now)
    credential = authority(db, device, now)
    wire = canonical_json(request)
    predicates = [DeviceCommand.request_id == str(request.request_id)]
    if isinstance(request, JobV2):
        predicates.append(DeviceCommand.idempotency_key == request.idempotency_key)
        # ResponseV2 and the node ledger forbid two offered jobs sharing an execution UUID.
        predicates.append(DeviceCommand.execution_id == str(request.execution_id))
    existing = db.scalars(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == device.id, or_(*predicates))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if existing:
        if (
            len(existing) != 1
            or existing[0].request_json != wire
            or existing[0].requester_user_id != user.id
            or existing[0].credential_generation != credential.token_hash
        ):
            raise HTTPException(409, "Request or idempotency key already used")
        if supersedes_command_id is not None:
            old = command_target(db, device.id, supersedes_command_id)
            if old is None or old.superseded_by != existing[0].id:
                raise HTTPException(409, "Supersession conflicts with original admission")
            authorize(user, old.action)
        return existing[0]
    caps = observation_caps(db, device.id, now)
    try:
        request.check_acceptance(device_id=device.id, capabilities=caps, now=now)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if request.expires_at - request.created_at > timedelta(hours=24):
        raise HTTPException(422, "Request lifetime exceeds 24 hours")
    rows = _reconcile_locked(db, device.id, now)
    prior = None
    if supersedes_command_id is not None:
        prior = command_target(db, device.id, supersedes_command_id)
        if prior is None:
            raise HTTPException(409, "Superseded command must belong to same device")
        authorize(user, prior.action)
        if prior.status != "queued" or prior.attempt != 0:
            raise HTTPException(409, "Only unclaimed queued commands can be superseded")
    if sum(r.status in ACTIVE for r in rows) - (1 if prior else 0) >= MAX_ACTIVE:
        raise HTTPException(409, "Device active command limit reached")
    item = DeviceCommand(
        id=str(uuid4()),
        device_id=device.id,
        request_id=str(request.request_id),
        execution_id=str(request.execution_id) if isinstance(request, JobV2) else None,
        idempotency_key=request.idempotency_key if isinstance(request, JobV2) else None,
        action=request.action,
        request_json=wire,
        request_sha256=sha256(wire.encode()).hexdigest(),
        requester_user_id=user.id,
        requested_by="user:" + user.id,
        credential_generation=credential.token_hash,
        status="queued",
        created_at=request.created_at,
        expires_at=request.expires_at,
        attempt=0,
    )
    db.add(item)
    db.flush()
    if prior:
        prior.status = "cancelled"
        prior.completed_at = now
        prior.superseded_by = item.id
        audit(db, prior, "command_superseded", user.id)
    audit(db, item, "command_queued", user.id)
    db.flush()
    return item


def cancel_command(db, command_id, user, *, now=None):
    # Non-locking lookup obtains parent ID only. Refresh actual command after parent lock.
    device_id = db.scalar(select(DeviceCommand.device_id).where(DeviceCommand.id == command_id))
    if device_id is None:
        raise HTTPException(404, "Command not found")
    if lock_repeater(db, device_id) is None:
        raise HTTPException(404, "Device not found")
    now = clock(now)
    _reconcile_locked(db, device_id, now)
    item = command_target(db, device_id, command_id)
    if item is None:
        raise HTTPException(404, "Command not found")
    authorize(user, item.action)
    if item.status != "queued" or item.attempt != 0:
        raise HTTPException(409, "Only unclaimed queued commands can be cancelled")
    item.status = "cancelled"
    item.completed_at = now
    audit(db, item, "command_cancelled", user.id)
    db.flush()
    return item


def claim_commands(db, device_id, capabilities, *, now=None):
    device = lock_repeater(db, device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    now = clock(now)
    rows = _reconcile_locked(db, device_id, now)
    candidates = [
        r
        for r in rows
        if r.status == "queued"
        and (
            r.lease_id is None
            or (r.execution_id is None and r.attempt < 3 and aware(r.lease_expires_at) <= now)
        )
    ]
    if not candidates:
        return []
    credential = authority(db, device, now)
    caps = observation_caps(db, device_id, now)
    delivered = []
    for item in candidates:
        if item.credential_generation != credential.token_hash:
            item.status = "failed"
            item.error_code = "credential_changed"
            item.completed_at = now
            audit(db, item, "command_authority_changed")
            continue
        req = stored_request(item)
        if (
            type(capabilities.get(req.action)) is not int
            or capabilities.get(req.action) != req.capability_version
            or type(caps.get(req.action)) is not int
            or caps.get(req.action) != req.capability_version
        ):
            continue
        if len(delivered) >= 16:
            break
        item.lease_id = str(uuid4())
        item.attempt += 1
        item.lease_issued_at = now
        item.lease_expires_at = min(now + timedelta(seconds=LEASE_SECONDS), aware(item.expires_at))
        db.add(
            DeviceCommandLease(
                id=item.lease_id,
                command_id=item.id,
                attempt=item.attempt,
                issued_at=item.lease_issued_at,
                expires_at=item.lease_expires_at,
                credential_generation=item.credential_generation,
            )
        )
        # The durable issuance record must exist before an offer can escape.
        db.flush()
        raw = req.model_dump(mode="json")
        raw.update(
            lease_id=item.lease_id, attempt=item.attempt, lease_expires_at=item.lease_expires_at
        )
        delivered.append(type(req).model_validate(raw))
        audit(db, item, "command_leased")
    db.flush()
    return delivered


def reconcile_result(db, device_id, result: ResultV2, *, now=None):
    """ACK known obsolete offers as history, never as authoritative completion.

    Caller owns commit. Parent -> command -> lease -> receipt lock ordering is
    shared with admission/live completion. Missing history is not inferred from
    an arbitrary client lease (migration backfills only the persisted current one).
    """
    device = lock_repeater(db, device_id)
    now = clock(now)
    credential = authority(db, device, now)
    item = db.scalar(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id, DeviceCommand.request_id == str(result.request_id)
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None or str(result.device_id) != device_id:
        raise HTTPException(409, "Result references unoffered command")
    if item.credential_generation != credential.token_hash:
        raise HTTPException(409, "Result credential generation mismatch")
    try:
        result.check_request(stored_request(item))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    lease = db.scalar(
        select(DeviceCommandLease)
        .where(
            DeviceCommandLease.id == str(result.lease_id), DeviceCommandLease.command_id == item.id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        lease is None
        or result.attempt != lease.attempt
        or lease.credential_generation != credential.token_hash
        or aware(lease.issued_at) < aware(item.created_at)
        or not aware(lease.issued_at) < aware(lease.expires_at) <= aware(item.expires_at)
        or result.sent_at < aware(lease.issued_at)
        or result.sent_at > now
        or (result.completed_at is not None and result.completed_at < aware(lease.issued_at))
    ):
        raise HTTPException(409, "Result issued lease/attempt/time mismatch")
    if str(result.lease_id) == item.lease_id:
        if (
            lease.attempt != item.attempt
            or aware(lease.issued_at) != aware(item.lease_issued_at)
            or aware(lease.expires_at) != aware(item.lease_expires_at)
        ):
            raise HTTPException(409, "Current lease history conflicts")
        return accept_result(db, device_id, result, now=now)
    if (
        item.lease_id is None
        or lease.attempt >= item.attempt
        or item.lease_issued_at is None
        or aware(lease.issued_at) >= aware(item.lease_issued_at)
    ):
        raise HTTPException(409, "Result is not a known older lease")
    current = db.scalar(
        select(DeviceCommandLease)
        .where(DeviceCommandLease.id == item.lease_id, DeviceCommandLease.command_id == item.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        current is None
        or current.attempt != item.attempt
        or current.credential_generation != credential.token_hash
        or aware(current.issued_at) != aware(item.lease_issued_at)
        or aware(current.expires_at) != aware(item.lease_expires_at)
    ):
        raise HTTPException(409, "Current lease history missing or conflicting")
    wire = canonical_json(result)
    digest = sha256(wire.encode()).hexdigest()
    receipts = db.scalars(
        select(DeviceCommandReceipt)
        .where(DeviceCommandReceipt.command_id == item.id)
        .limit(MAX_RECEIPTS + 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for receipt in receipts:
        if receipt.result_sha256 == digest:
            if receipt.result_json != wire:
                raise HTTPException(409, "Result digest conflict")
            return ResultAcceptanceV2(
                request_id=result.request_id,
                execution_id=result.execution_id,
                acceptance_id=receipt.acceptance_id,
                result_sha256=digest,
                disposition=receipt.disposition,
            )
    if len(receipts) >= MAX_RECEIPTS:
        raise HTTPException(409, "Command result receipt limit reached")
    receipt = DeviceCommandReceipt(
        acceptance_id=str(uuid4()),
        command_id=item.id,
        result_json=wire,
        result_sha256=digest,
        lease_id=lease.id,
        attempt=lease.attempt,
        accepted_at=now,
        disposition="superseded",
    )
    db.add(receipt)
    audit(db, item, "command_result_archived")
    db.flush()
    return ResultAcceptanceV2(
        request_id=result.request_id,
        execution_id=result.execution_id,
        acceptance_id=receipt.acceptance_id,
        result_sha256=digest,
        disposition="superseded",
    )


def accept_result(db, device_id, result: ResultV2, *, now=None):
    device = lock_repeater(db, device_id)
    now = clock(now)
    credential = authority(db, device, now)
    item = db.scalar(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id, DeviceCommand.request_id == str(result.request_id)
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None or str(result.device_id) != device_id:
        raise HTTPException(409, "Result references unoffered command")
    if item.credential_generation != credential.token_hash:
        raise HTTPException(409, "Result credential generation mismatch")
    req = stored_request(item)
    try:
        result.check_request(req)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    target = {"accepted": "received", "unsupported": "failed", "conflict": "failed"}.get(
        result.status, result.status
    )
    lease = None
    if item.status == "unknown" and target == "unknown":
        # Acquire optional history before receipts to preserve the shared lock
        # order. Validate it only for a new result below: retained exact ACKs
        # do not depend on history or offer metadata still being available.
        lease = db.scalar(
            select(DeviceCommandLease)
            .where(DeviceCommandLease.id == item.lease_id, DeviceCommandLease.command_id == item.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    wire = canonical_json(result)
    digest = sha256(wire.encode()).hexdigest()
    receipts = db.scalars(
        select(DeviceCommandReceipt)
        .where(DeviceCommandReceipt.command_id == item.id)
        .limit(MAX_RECEIPTS + 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for receipt in receipts:
        if receipt.result_sha256 == digest and receipt.result_json == wire:
            return ResultAcceptanceV2(
                request_id=result.request_id,
                execution_id=result.execution_id,
                acceptance_id=receipt.acceptance_id,
                result_sha256=digest,
                disposition=receipt.disposition,
            )
    if (
        result.lease_id is None
        or str(result.lease_id) != item.lease_id
        or result.attempt != item.attempt
        or item.lease_issued_at is None
        or result.sent_at < aware(item.lease_issued_at)
        or result.sent_at > now
        or (result.completed_at is not None and result.completed_at < aware(item.lease_issued_at))
    ):
        raise HTTPException(409, "Result lease/attempt/time mismatch")
    if len(receipts) >= MAX_RECEIPTS:
        raise HTTPException(409, "Command result receipt limit reached")
    if item.status == "unknown" and target == "unknown":
        # Deadline uncertainty is not a node result phase. A recovered executor
        # can acknowledge its first durable UNKNOWN on the original current
        # offer without claiming completion or replaying a nonretryable effect.
        if (
            lease is None
            or lease.attempt != item.attempt
            or lease.credential_generation != credential.token_hash
            or aware(lease.issued_at) != aware(item.lease_issued_at)
            or aware(lease.expires_at) != aware(item.lease_expires_at)
            or aware(lease.issued_at) < aware(item.created_at)
            or not aware(lease.issued_at) < aware(lease.expires_at) <= aware(item.expires_at)
        ):
            raise HTTPException(409, "Current lease history missing or conflicting")
    if item.status in TERMINAL or (
        item.status == "unknown"
        and target not in {"succeeded", "failed", "awaiting_verification", "unknown"}
    ):
        raise HTTPException(409, "Result conflicts with command state")
    if (
        item.status != "unknown"
        and target in PROGRESS
        and PROGRESS[target] <= PROGRESS.get(item.status, -1)
    ):
        raise HTTPException(409, "Result is not a monotonic transition")
    if item.result_json is not None:
        last = ResultV2.model_validate(json.loads(item.result_json))
        if result.sent_at < last.sent_at:
            raise HTTPException(409, "Result predates last accepted phase")
        previous = {"accepted": "received", "unsupported": "failed", "conflict": "failed"}.get(
            last.status, last.status
        )
        if (
            item.status == target == previous == "unknown"
            and last.lease_id == result.lease_id
            and last.attempt == result.attempt
        ):
            # Exact wire retries returned their retained receipt above. Changing
            # a timestamp/body alone cannot consume another bounded phase.
            raise HTTPException(409, "Result repeats uncertainty without a state change")
        if (
            last.lease_id == result.lease_id
            and last.attempt == result.attempt
            and target in PROGRESS
            and previous in PROGRESS
            and PROGRESS[target] <= PROGRESS[previous]
        ):
            raise HTTPException(409, "Result regresses original lease progress")
    if item.status not in ACTIVE and target in ACTIVE:
        # Parent lock serializes this with admission. Do not reconcile here: a
        # rejected result must not mutate deadlines, receipts or audit history.
        active_count = db.scalar(
            select(func.count())
            .select_from(DeviceCommand)
            .where(DeviceCommand.device_id == device_id, DeviceCommand.status.in_(ACTIVE))
        )
        if active_count >= MAX_ACTIVE:
            raise HTTPException(409, "Device active command limit reached")
    # Expired original leases can resolve uncertainty until a query is reclaimed.
    # Lease identity, not an arbitrary deadline extension, proves the original offer.
    receipt = DeviceCommandReceipt(
        acceptance_id=str(uuid4()),
        command_id=item.id,
        result_json=wire,
        result_sha256=digest,
        lease_id=item.lease_id,
        attempt=item.attempt,
        accepted_at=now,
        disposition="accepted",
    )
    db.add(receipt)
    item.status = target
    item.result_json = wire
    item.result_sha256 = digest
    item.acceptance_id = receipt.acceptance_id
    item.completed_at = result.completed_at
    for field in ("persisted", "applied", "restart_required"):
        value = getattr(result, field)
        if value is not None:
            setattr(item, field, value)
    item.error_code = result.error_code or (
        result.status if result.status in {"unsupported", "conflict"} else None
    )
    audit(db, item, "command_result_accepted")
    db.flush()
    return ResultAcceptanceV2(
        request_id=result.request_id,
        execution_id=result.execution_id,
        acceptance_id=receipt.acceptance_id,
        result_sha256=digest,
    )
