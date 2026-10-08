"""Focused source-only lifecycle tests; SQLite is not a row-lock proof."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.contracts.v2.command import JobV2, QueryV2, ResultV2
from app.db.base import Base
from app.db.models import (
    AuditLog,
    Certificate,
    DeviceCommand,
    DeviceCommandReceipt,
    DeviceCredential,
    DeviceObservation,
    Repeater,
    User,
)
from app.services.command_dispatch import (
    accept_result,
    admit_command,
    cancel_command,
    claim_commands,
)
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 8, tzinfo=UTC)
CAPS = {"diagnostic.read": 1, "config.read": 1, "set_mode": 1}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def seed(db):
    device = Repeater(
        id=str(uuid4()),
        node_name="synthetic",
        pubkey="ab" * 32,
        status="adopted",
        cert_serial="test",
    )
    user = User(
        id=str(uuid4()), email="synthetic@example.invalid", password_hash="unused", role="operator"
    )
    db.add_all([device, user])
    db.flush()
    db.add_all(
        [
            DeviceCredential(
                repeater_id=device.id,
                token_hash="a" * 64,
                csr_public_key_sha256="b" * 64,
                cert_serial="test",
            ),
            Certificate(repeater_id=device.id, serial="test", expires_at=NOW + timedelta(days=1)),
            DeviceObservation(
                repeater_id=device.id,
                boot_id=str(uuid4()),
                sent_at=NOW,
                received_at=NOW,
                capabilities_json=json.dumps(CAPS),
                inventory_json="{}",
                telemetry_json="{}",
            ),
        ]
    )
    db.commit()
    return device, user


def request(device, job=False, **changes):
    raw = dict(
        version=2,
        type="query",
        device_id=device.id,
        request_id=str(uuid4()),
        action="diagnostic.read",
        capability_version=1,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        params={},
    )
    if job:
        raw.update(
            type="job",
            action="set_mode",
            execution_id=str(uuid4()),
            idempotency_key=str(uuid4()),
            params={"mode": "monitor"},
        )
    raw.update(changes)
    return (JobV2 if job else QueryV2).model_validate(raw)


def result(delivery, status="succeeded", **changes):
    raw = dict(
        version=2,
        type="result",
        device_id=delivery.device_id,
        boot_id=str(uuid4()),
        request_id=delivery.request_id,
        execution_id=getattr(delivery, "execution_id", None),
        status=status,
        sent_at=NOW + timedelta(seconds=2),
        completed_at=NOW + timedelta(seconds=2)
        if status in {"succeeded", "failed", "unsupported", "conflict"}
        else None,
        lease_id=delivery.lease_id,
        attempt=delivery.attempt,
    )
    raw.update(changes)
    return ResultV2.model_validate(raw)


def test_admission_binds_user_and_exact_replay(db):
    device, user = seed(db)
    req = request(device)
    item = admit_command(db, req, user, now=NOW)
    db.commit()
    assert item.requested_by == "user:" + user.id and item.requester_user_id == user.id
    assert admit_command(db, req, user, now=NOW).id == item.id
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
    changed = req.model_dump(mode="json")
    changed["expires_at"] = (NOW + timedelta(hours=2)).isoformat()
    with pytest.raises(HTTPException) as exc:
        admit_command(db, QueryV2.model_validate(changed), user, now=NOW)
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "credential",
        "certificate",
        "serial",
        "freshness",
        "capability",
        "future_ingest",
        "role",
    ],
)
def test_admission_fails_closed(db, mutation):
    device, user = seed(db)
    if mutation == "status":
        device.status = "pending"
    if mutation == "credential":
        db.get(DeviceCredential, device.id).revoked_at = NOW
    if mutation == "certificate":
        db.scalar(select(Certificate)).expires_at = NOW
    if mutation == "serial":
        device.cert_serial = "other"
    if mutation == "freshness":
        db.get(DeviceObservation, device.id).received_at = NOW - timedelta(seconds=121)
    if mutation == "future_ingest":
        db.get(DeviceObservation, device.id).received_at = NOW + timedelta(seconds=1)
    if mutation == "capability":
        db.get(DeviceObservation, device.id).capabilities_json = "{}"
    if mutation == "role":
        user.role = "viewer"
    db.commit()
    with pytest.raises(HTTPException):
        admit_command(db, request(device, job=True), user, now=NOW)
    assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


def test_lost_response_safe_read_reclaim_stale_result(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    first = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    assert first.attempt == 1 and first.lease_expires_at <= first.expires_at
    assert claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=1)) == []
    second = claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=61))[0]
    db.commit()
    assert (
        second.request_id == first.request_id
        and second.lease_id != first.lease_id
        and second.attempt == 2
    )
    with pytest.raises(HTTPException):
        accept_result(db, device.id, result(first), now=NOW + timedelta(seconds=62))
    db.rollback()
    assert db.get(DeviceCommand, item.id).attempt == 2


def test_exact_historical_duplicate_ack_no_extra_audit(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    progress = result(delivery, "received")
    ack = accept_result(db, device.id, progress, now=NOW + timedelta(seconds=3))
    db.commit()
    done = result(
        delivery, sent_at=NOW + timedelta(seconds=4), completed_at=NOW + timedelta(seconds=4)
    )
    accept_result(db, device.id, done, now=NOW + timedelta(seconds=5))
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    assert accept_result(db, device.id, progress, now=NOW + timedelta(seconds=6)) == ack
    assert ack.acceptance_id and len(ack.result_sha256) == 64
    assert db.get(DeviceCommand, item.id).status == "succeeded"
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 2
    with pytest.raises(HTTPException):
        accept_result(
            db, device.id, result(delivery, message="tampered"), now=NOW + timedelta(seconds=6)
        )


@pytest.mark.parametrize("mode", ["forward", "monitor", "no_tx"])
def test_disruptive_jobs_unknown_no_blind_retry_and_late_resolution(db, mode):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True, params={"mode": mode}), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    assert claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=61)) == []
    db.commit()
    assert item.status == "unknown" and item.completed_at is None
    accept_result(db, device.id, result(delivery), now=NOW + timedelta(seconds=62))
    db.commit()
    assert item.status == "succeeded"


def test_awaiting_verification_flags_not_success_and_conflict_mapping(db):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    accept_result(
        db,
        device.id,
        result(
            delivery, "awaiting_verification", persisted=True, applied=False, restart_required=True
        ),
        now=NOW + timedelta(seconds=3),
    )
    db.commit()
    assert (
        item.status == "awaiting_verification"
        and item.persisted is True
        and item.applied is False
        and item.restart_required is True
    )
    accept_result(
        db,
        device.id,
        result(
            delivery,
            "conflict",
            sent_at=NOW + timedelta(seconds=4),
            completed_at=NOW + timedelta(seconds=4),
            error_code="revision_conflict",
        ),
        now=NOW + timedelta(seconds=5),
    )
    db.commit()
    assert item.status == "failed" and item.error_code == "revision_conflict"


def test_cancel_supersede_queued_only_and_current_poll_caps(db):
    device, user = seed(db)
    old = admit_command(db, request(device), user, now=NOW)
    db.commit()
    new = admit_command(db, request(device), user, now=NOW, supersedes_command_id=old.id)
    db.commit()
    assert old.status == "cancelled" and old.superseded_by == new.id
    assert claim_commands(db, device.id, {}, now=NOW) == []
    assert new.status == "queued"
    claim_commands(db, device.id, CAPS, now=NOW)
    db.commit()
    with pytest.raises(HTTPException):
        cancel_command(db, new.id, user, now=NOW)


def test_queued_expiry_and_query_attempt_bound(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    for seconds in [0, 61, 122]:
        db.get(DeviceObservation, device.id).received_at = NOW + timedelta(seconds=seconds)
        db.commit()
        assert (
            claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=seconds))[0].attempt
            == seconds // 61 + 1
        )
        db.commit()
    assert claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=183)) == []
    assert item.status == "unknown"
    db.get(DeviceObservation, device.id).received_at = NOW
    db.commit()
    unclaimed = admit_command(
        db, request(device, expires_at=NOW + timedelta(seconds=10)), user, now=NOW
    )
    db.commit()
    claim_commands(db, device.id, {}, now=NOW + timedelta(seconds=11))
    db.commit()
    assert unclaimed.status == "expired"


@pytest.mark.parametrize(
    "field,value",
    [
        ("lease_id", None),
        ("attempt", 2),
        ("lease_id", str(uuid4())),
        ("execution_id", str(uuid4())),
        ("request_id", str(uuid4())),
        ("device_id", str(uuid4())),
        ("sent_at", NOW - timedelta(seconds=1)),
        ("sent_at", NOW + timedelta(minutes=10)),
    ],
)
def test_result_binding_rejects_without_mutation_or_ack(db, field, value):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    changes = {field: value}
    if field == "lease_id" and value is None:
        changes["attempt"] = None
    if field == "sent_at":
        changes["completed_at"] = value
    with pytest.raises(HTTPException):
        accept_result(db, device.id, result(delivery, **changes), now=NOW + timedelta(seconds=3))
    assert item.status == "queued" and item.result_json is None
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0


def test_credential_generation_change_rejects_result_and_unclaimed_job(db):
    device, user = seed(db)
    offered = admit_command(db, request(device), user, now=NOW)
    unclaimed = admit_command(db, request(device, job=True), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, {"diagnostic.read": 1}, now=NOW)[0]
    db.commit()
    db.get(DeviceCredential, device.id).token_hash = "c" * 64
    db.commit()
    with pytest.raises(HTTPException):
        accept_result(db, device.id, result(delivery), now=NOW + timedelta(seconds=3))
    db.rollback()
    assert claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=4)) == []
    assert db.get(DeviceCommand, unclaimed.id).status == "failed"
    assert db.get(DeviceCommand, offered.id).result_json is None


def test_safe_query_receipts_bounded_without_false_ack(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    for attempt in range(3):
        seconds = attempt * 61
        db.get(DeviceObservation, device.id).received_at = NOW + timedelta(seconds=seconds)
        db.commit()
        delivery = claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=seconds))[0]
        db.commit()
        for phase, name in enumerate(["received", "running", "awaiting_verification"]):
            offered = result(delivery, name, sent_at=NOW + timedelta(seconds=seconds + phase + 1))
            if attempt == 2 and phase == 2:
                with pytest.raises(HTTPException) as exc:
                    accept_result(
                        db, device.id, offered, now=NOW + timedelta(seconds=seconds + phase + 2)
                    )
                assert exc.value.status_code == 409
                assert item.status == "running"
            else:
                accept_result(
                    db, device.id, offered, now=NOW + timedelta(seconds=seconds + phase + 2)
                )
                db.commit()
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"created_at": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
        {"expires_at": NOW + timedelta(hours=25)},
    ],
)
def test_admission_time_bounds(db, changes):
    device, user = seed(db)
    with pytest.raises((HTTPException, ValueError)):
        admit_command(db, request(device, **changes), user, now=NOW)
    assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


def test_viewer_reads_and_job_key_reuse(db):
    device, user = seed(db)
    user.role = "viewer"
    db.commit()
    admit_command(db, request(device), user, now=NOW)
    db.commit()
    with pytest.raises(HTTPException):
        admit_command(db, request(device, job=True), user, now=NOW)
    user.role = "operator"
    db.commit()
    first = request(device, job=True)
    item = admit_command(db, first, user, now=NOW)
    db.commit()
    assert admit_command(db, first, user, now=NOW).id == item.id
    with pytest.raises(HTTPException):
        admit_command(
            db, request(device, job=True, execution_id=str(first.execution_id)), user, now=NOW
        )
    with pytest.raises(HTTPException):
        admit_command(
            db, request(device, job=True, idempotency_key=first.idempotency_key), user, now=NOW
        )


def test_poll_limit_queue_bound_and_unsupported_cannot_queue(db):
    device, user = seed(db)
    for _ in range(256):
        admit_command(db, request(device), user, now=NOW)
    db.commit()
    with pytest.raises(HTTPException):
        admit_command(db, request(device), user, now=NOW)
    assert len(claim_commands(db, device.id, CAPS, now=NOW)) == 16
    with pytest.raises(ValueError):
        request(device, action="shell.execute")
    with pytest.raises(ValueError):
        request(device, capability_version=2)


def test_inform_atomic_rollback_and_legacy_queue_isolation(db, monkeypatch):
    import importlib

    from app.db.models import CommandQueueItem

    module = importlib.import_module("app.api.routes.inform_v2")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=3)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    legacy = CommandQueueItem(repeater_id=device.id, command="restart_service", status="queued")
    db.add(legacy)
    db.commit()
    raw = dict(
        version=2,
        type="inform",
        device_id=device.id,
        boot_id=str(uuid4()),
        sent_at=NOW,
        node_name=device.node_name,
        software_version="synthetic",
        capabilities=CAPS,
        inventory={"radios": [], "identities": [], "sensors": [], "plugins": []},
        telemetry={},
        results=[],
    )
    response = module.inform_v2(raw=raw, device=device, db=db)
    assert len(response.queries) == 1 and response.jobs == () and response.accepted_results == ()
    assert legacy.status == "queued"
    observation = db.get(DeviceObservation, device.id)
    previous_boot = observation.boot_id
    raw.update(
        boot_id=str(uuid4()),
        sent_at=NOW + timedelta(seconds=3),
        telemetry={"changed": True},
        results=[result(response.queries[0], attempt=2).model_dump(mode="json")],
    )
    with pytest.raises(HTTPException):
        module.inform_v2(raw=raw, device=device, db=db)
    assert db.get(DeviceObservation, device.id).boot_id == previous_boot
    assert db.get(DeviceCommand, item.id).result_json is None
    raw["results"] = [
        result(
            response.queries[0],
            sent_at=NOW + timedelta(seconds=3),
            completed_at=NOW + timedelta(seconds=3),
        ).model_dump(mode="json")
    ]
    accepted = module.inform_v2(raw=raw, device=device, db=db)
    assert accepted.accepted_results[0].acceptance_id
    assert db.get(DeviceCommand, item.id).status == "succeeded"
    duplicate = module.inform_v2(raw=raw, device=device, db=db)
    assert duplicate.accepted_results == accepted.accepted_results


def test_status_reconcile_preserves_original_offer_until_actual_reclaim(db):
    from app.services.command_dispatch import reconcile_commands

    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    db.commit()
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
    db.commit()
    assert item.lease_id == str(delivery.lease_id)
    with pytest.raises(HTTPException):
        cancel_command(db, item.id, user, now=NOW + timedelta(seconds=61))
    ack = accept_result(db, device.id, result(delivery), now=NOW + timedelta(seconds=62))
    db.commit()
    assert ack.acceptance_id and item.status == "succeeded"


def test_append_only_migration_runs_with_semicolon_runner():
    from app.db.migrate import apply_migrations
    from sqlalchemy import inspect, text

    engine = create_engine("sqlite://")
    apply_migrations(engine)
    apply_migrations(engine)
    inspector = inspect(engine)
    assert {"device_commands", "device_command_receipts"} <= set(inspector.get_table_names())
    assert "received_at" in {c["name"] for c in inspector.get_columns("device_observations")}
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM schema_migrations WHERE version = '0019_device_commands'"
                )
            )
            == 1
        )
    with Session(engine) as db:
        device, user = seed(db)
        item = admit_command(db, request(device), user, now=NOW)
        db.commit()
        assert db.get(DeviceCommand, item.id).status == "queued"
    engine.dispose()


def test_glass_wire_receipt_digest_lease_fields_and_tamper_detection():
    from app.contracts.v2.command import ResultAcceptanceV2, result_sha256
    from app.contracts.v2.telemetry import InformV2, ResponseV2

    from test_protocol_v2 import fixture, query

    raw = query()
    assert "lease_id" not in QueryV2.model_validate(raw).model_dump(mode="json")
    raw.update(lease_id=str(uuid4()), attempt=1, lease_expires_at="2026-01-01T00:01:00Z")
    parsed = QueryV2.model_validate(raw)
    assert parsed.attempt == 1
    for changes in [
        {"attempt": True},
        {"attempt": "1"},
        {"attempt": 4},
        {"lease_expires_at": "2026-01-01T00:06:00Z"},
        {"lease_id": None},
    ]:
        with pytest.raises(ValueError):
            QueryV2.model_validate({**raw, **changes})
    inform_raw = fixture("proposed_v2_inform.json")
    offered = fixture("proposed_v2_result.json")
    offered.update(lease_id=str(uuid4()), attempt=1)
    inform_raw.update(results=[offered], sent_at=offered["sent_at"])
    inform = InformV2.model_validate(inform_raw)
    digest = result_sha256(inform.results[0])
    response_raw = fixture("proposed_v2_response.json")
    response_raw.update(
        sent_at=inform_raw["sent_at"],
        accepted_results=[
            dict(
                request_id=offered["request_id"],
                execution_id=offered["execution_id"],
                acceptance_id=str(uuid4()),
                result_sha256=digest,
            )
        ],
    )
    ResponseV2.model_validate(response_raw).check_inform(inform)
    response_raw["accepted_results"][0]["result_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        ResponseV2.model_validate(response_raw).check_inform(inform)
    with pytest.raises(ValueError):
        ResultAcceptanceV2(
            request_id=offered["request_id"],
            execution_id=offered["execution_id"],
            acceptance_id=str(uuid4()),
        )


def test_api_auth_authority_routes_no_private_hash(db):
    from app.api.routes.commands import router
    from app.api.routes.inform_v2 import router as inform_router
    from app.db.models import AuthToken
    from app.db.session import get_db_session
    from app.security.tokens import hash_token
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    now = datetime.now(UTC)
    app = FastAPI()
    app.include_router(router)
    app.include_router(inform_router)

    # Shared in-memory SQLite is used only for in-process HTTP route testing.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy").pool.StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as api_db:
        dev, actor = seed(api_db)
        api_db.get(DeviceObservation, dev.id).received_at = now
        api_db.scalar(select(Certificate)).expires_at = now + timedelta(days=1)
        device_token = "d" * 43
        api_db.get(DeviceCredential, dev.id).token_hash = hash_token(device_token)
        token = "synthetic-offline-user-token"
        api_db.add(
            AuthToken(
                user_id=actor.id, token_hash=hash_token(token), expires_at=now + timedelta(hours=1)
            )
        )
        api_db.commit()
        app.dependency_overrides[get_db_session] = lambda: api_db
        raw = request(dev, created_at=now, expires_at=now + timedelta(hours=1)).model_dump(
            mode="json"
        )
        with TestClient(app, base_url="https://testserver") as client:
            assert client.post("/api/commands/v2", json=raw).status_code == 401
            client.headers["Authorization"] = "Bearer " + token
            forged = {**raw, "requested_by": "admin"}
            assert client.post("/api/commands/v2", json=forged).status_code == 422
            response = client.post("/api/commands/v2", json=raw)
            assert response.status_code == 201, response.text
            command = response.json()
            command_id = command["command_id"]
            assert command["requested_by"] == "user:" + actor.id
            assert (
                "credential_generation" not in response.text and "token_hash" not in response.text
            )
            assert client.post("/api/commands/v2", json=raw).json()["command_id"] == command_id
            assert client.get("/api/commands/v2").json()[0]["command_id"] == command_id
            assert client.get("/api/commands/v2/" + command_id).status_code == 200
            assert (
                client.post("/api/commands/v2/" + command_id + "/cancel").json()["status"]
                == "cancelled"
            )
            assert (
                client.post(
                    "/api/commands",
                    json={
                        "node_name": dev.node_name,
                        "action": "restart_service",
                        "params": {},
                        "requested_by": "forged",
                    },
                ).status_code
                == 409
            )
            actor.role = "viewer"
            api_db.commit()
            job_raw = request(
                dev, job=True, created_at=now, expires_at=now + timedelta(hours=1)
            ).model_dump(mode="json")
            assert client.post("/api/commands/v2", json=job_raw).status_code == 403
            read_raw = request(dev, created_at=now, expires_at=now + timedelta(hours=1)).model_dump(
                mode="json"
            )
            queued = client.post("/api/commands/v2", json=read_raw)
            assert queued.status_code == 201
            inform_raw = dict(
                version=2,
                type="inform",
                device_id=dev.id,
                boot_id=str(uuid4()),
                sent_at=datetime.now(UTC).isoformat(),
                node_name=dev.node_name,
                software_version="synthetic",
                capabilities=CAPS,
                inventory={"radios": [], "identities": [], "sensors": [], "plugins": []},
                telemetry={},
                results=[],
            )
            assert client.post("/inform/v2", json=inform_raw).status_code == 401
            client.headers["Authorization"] = "Bearer " + device_token
            response = client.post("/inform/v2", json=inform_raw)
            assert response.status_code == 200, response.text
            delivery = QueryV2.model_validate(response.json()["queries"][0])
            sent = datetime.now(UTC)
            offered = result(delivery, sent_at=sent, completed_at=sent).model_dump(mode="json")
            inform_raw.update(sent_at=sent.isoformat(), results=[offered])
            accepted = client.post("/inform/v2", json=inform_raw)
            assert accepted.status_code == 200, accepted.text
            receipt = accepted.json()["accepted_results"][0]
            assert receipt["acceptance_id"] and receipt["result_sha256"]
            assert (
                client.post("/inform/v2", json=inform_raw).json()["accepted_results"][0] == receipt
            )
            client.headers["Authorization"] = "Bearer " + token
            assert (
                client.get("/api/commands/v2/" + queued.json()["command_id"]).json()["status"]
                == "succeeded"
            )
    engine.dispose()


def bulk_commands(db, device, user, count, status="queued"):
    """Synthetic rows, not admissions: exercise large histories and corrupt invariants."""
    rows = []
    for _ in range(count):
        req = request(device)
        rows.append(
            DeviceCommand(
                id=str(uuid4()),
                device_id=device.id,
                request_id=str(req.request_id),
                action=req.action,
                request_json=req.model_dump_json(),
                request_sha256="a" * 64,
                requester_user_id=user.id,
                requested_by="user:" + user.id,
                credential_generation="a" * 64,
                status=status,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                attempt=0,
            )
        )
    db.add_all(rows)
    db.commit()
    return rows


@pytest.mark.parametrize("history_size", [300, 1000])
def test_reconciliation_bounds_active_not_history_and_exact_inactive_targets(
    db, history_size, monkeypatch
):
    from app.api.routes.commands import get_command_v2
    from app.services.command_dispatch import command_rows, reconcile_commands

    monkeypatch.setattr(
        "app.services.command_dispatch.clock", lambda now: NOW if now is None else now
    )

    device, user = seed(db)
    history = bulk_commands(db, device, user, history_size, "succeeded")
    fresh = admit_command(db, request(device), user, now=NOW)
    db.commit()
    assert [r.id for r in command_rows(db, device.id)] == [fresh.id]
    assert get_command_v2(history[-1].id, db=db, _=user).status == "succeeded"
    with pytest.raises(HTTPException) as exc:
        cancel_command(db, history[-1].id, user, now=NOW)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        admit_command(db, request(device), user, now=NOW, supersedes_command_id=history[0].id)
    assert exc.value.detail == "Only unclaimed queued commands can be superseded"
    assert len(reconcile_commands(db, device.id, now=NOW)) == 1
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    accept_result(db, device.id, result(delivery, "running"), now=NOW + timedelta(seconds=3))
    assert fresh.status == "running"


def test_active_overflow_sentinel_fails_closed_before_deadline_mutation(db):
    from app.services.command_dispatch import reconcile_commands

    device, user = seed(db)
    rows = bulk_commands(db, device, user, 257)
    rows[0].expires_at = NOW
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(HTTPException) as exc:
        reconcile_commands(db, device.id, now=NOW)
    assert exc.value.status_code == 409
    assert rows[0].status == "queued"
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before


def test_unknown_reactivation_capacity_rejects_without_receipt_audit_terminal_allowed(db):
    from app.services.command_dispatch import reconcile_commands

    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    progress = result(delivery, "received")
    ack = accept_result(db, device.id, progress, now=NOW + timedelta(seconds=3))
    db.commit()
    reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
    db.commit()
    bulk_commands(db, device, user, 256)
    before = db.scalar(select(func.count()).select_from(AuditLog))
    late = result(delivery, "awaiting_verification", sent_at=NOW + timedelta(seconds=62))
    with pytest.raises(HTTPException) as exc:
        accept_result(db, device.id, late, now=NOW + timedelta(seconds=63))
    assert exc.value.status_code == 409
    assert item.status == "unknown" and item.acceptance_id == str(ack.acceptance_id)
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before
    assert accept_result(db, device.id, progress, now=NOW + timedelta(seconds=63)) == ack
    done = result(
        delivery, sent_at=NOW + timedelta(seconds=64), completed_at=NOW + timedelta(seconds=64)
    )
    accept_result(db, device.id, done, now=NOW + timedelta(seconds=65))
    assert item.status == "succeeded"
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 2


@pytest.mark.parametrize("scope", ["device", "global"])
@pytest.mark.parametrize("target", ["expired", "unknown", "queued"])
def test_status_filter_applies_deadlines_before_page_selection(db, monkeypatch, scope, target):
    import app.services.command_dispatch as dispatch
    from app.api.routes.commands import list_commands_v2
    from sqlalchemy import event

    device, user = seed(db)
    history = bulk_commands(db, device, user, 300, "succeeded")
    for row in history:
        row.created_at = NOW + timedelta(seconds=30)
    candidates = bulk_commands(db, device, user, 3)
    for row in candidates:
        if target == "expired":
            row.expires_at = NOW
        else:
            row.status = "running"
            row.lease_id = str(uuid4())
            row.lease_issued_at = NOW
            row.lease_expires_at = NOW + timedelta(seconds=60)
            row.attempt = 3 if target == "unknown" else 1
    db.commit()
    monkeypatch.setattr(dispatch, "clock", lambda now: NOW + timedelta(seconds=61))
    monkeypatch.setattr("app.api.routes.commands.clock", lambda now: NOW + timedelta(seconds=61))
    statements = []

    def observe(state):
        if state.is_select:
            statements.append(state.statement)

    event.listen(db, "do_orm_execute", observe)
    try:
        page = list_commands_v2(
            status_filter=target,
            device_id=device.id if scope == "device" else None,
            limit=1,
            offset=1,
            db=db,
            _=user,
        )
    finally:
        event.remove(db, "do_orm_execute", observe)
    assert len(page) == 1 and page[0].status == target
    assert page[0].command_id == sorted(r.id for r in candidates)[1]
    locked = [
        s
        for s in statements
        if getattr(s, "_for_update_arg", None) is not None and "device_commands" in str(s)
    ]
    assert locked and all(s._limit_clause is not None for s in locked)
    assert all("status IN" in str(s) for s in locked)
    assert all(s._limit_clause.value == 257 for s in locked)
    if scope == "global":
        selection = next(s for s in statements if "CASE" in str(s))
        assert selection._limit_clause.value == 1 and selection._offset_clause.value == 1
        assert "status IN" in str(selection)


def test_sql_effective_status_matches_reconciliation_for_all_deadline_branches(db):
    from app.services.command_dispatch import effective_status, reconcile_commands

    device, user = seed(db)
    states = [
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
    rows = bulk_commands(db, device, user, len(states) * 12)
    for index, row in enumerate(rows):
        state_index, scenario = divmod(index, 12)
        row.status = states[state_index]
        row.expires_at = NOW + timedelta(seconds=120 if scenario % 2 else 30)
        row.execution_id = str(uuid4()) if scenario % 4 >= 2 else None
        row.attempt = 3 if scenario % 3 == 0 else 1
        if scenario >= 4:
            row.lease_id = str(uuid4())
            row.lease_issued_at = NOW
            row.lease_expires_at = NOW + timedelta(seconds=30 if scenario < 8 else 120)
    db.commit()
    now = NOW + timedelta(seconds=61)
    expected = dict(db.execute(select(DeviceCommand.id, effective_status(now))).all())
    reconcile_commands(db, device.id, now=now)
    assert {r.id: r.status for r in rows} == expected


def test_global_page_locks_only_selected_parents_in_sorted_order(db, monkeypatch):
    import app.services.command_dispatch as dispatch
    from app.api.routes.commands import list_commands_v2

    first, user = seed(db)
    devices = [first]
    for index in range(2):
        device = Repeater(
            id=str(uuid4()),
            node_name="page-" + str(index),
            pubkey=str(index) * 64,
            status="adopted",
        )
        db.add(device)
        db.flush()
        devices.append(device)
    candidates = []
    for index, device in enumerate(devices):
        row = bulk_commands(db, device, user, 1)[0]
        row.created_at = NOW + timedelta(seconds=index)
        row.expires_at = NOW + timedelta(seconds=10)
        candidates.append(row)
    db.commit()
    acquired = []
    original = dispatch.lock_repeater

    def track(session, device_id):
        acquired.append(device_id)
        return original(session, device_id)

    monkeypatch.setattr(dispatch, "lock_repeater", track)
    monkeypatch.setattr("app.api.routes.commands.clock", lambda now: NOW + timedelta(seconds=61))
    monkeypatch.setattr(dispatch, "clock", lambda now: NOW + timedelta(seconds=61))
    page = list_commands_v2(
        status_filter="expired", device_id=None, limit=2, offset=0, db=db, _=user
    )
    assert [r.command_id for r in page] == [r.id for r in reversed(candidates[1:])]
    assert acquired == sorted(d.id for d in devices[1:])
    assert candidates[0].status == "queued"


@pytest.mark.parametrize("boundary", ["request", "certificate", "capabilities"])
def test_admission_implicit_clock_after_parent_wait(db, monkeypatch, boundary):
    import app.services.command_dispatch as dispatch

    device, user = seed(db)
    req = request(device, expires_at=NOW + timedelta(seconds=10))
    if boundary == "certificate":
        db.scalar(select(Certificate)).expires_at = NOW + timedelta(seconds=10)
        req = request(device)
    elif boundary == "capabilities":
        req = request(device)
    db.commit()
    current = [NOW]
    original = dispatch.lock_repeater

    def waited(session, device_id):
        parent = original(session, device_id)
        current[0] = NOW + timedelta(seconds=121 if boundary == "capabilities" else 11)
        return parent

    monkeypatch.setattr(dispatch, "lock_repeater", waited)
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    with pytest.raises(HTTPException) as exc:
        admit_command(db, req, user)
    assert exc.value.status_code == 409
    assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize("operation", ["cancel", "supersede", "claim", "reconcile"])
def test_implicit_deadline_clock_after_parent_wait(db, monkeypatch, operation):
    import app.services.command_dispatch as dispatch

    device, user = seed(db)
    old = admit_command(db, request(device, expires_at=NOW + timedelta(seconds=10)), user, now=NOW)
    db.commit()
    current = [NOW]
    original = dispatch.lock_repeater

    def waited(session, device_id):
        parent = original(session, device_id)
        current[0] = NOW + timedelta(seconds=11)
        return parent

    monkeypatch.setattr(dispatch, "lock_repeater", waited)
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    if operation in {"cancel", "supersede"}:
        with pytest.raises(HTTPException) as exc:
            if operation == "cancel":
                cancel_command(db, old.id, user)
            else:
                admit_command(db, request(device), user, supersedes_command_id=old.id)
        assert exc.value.status_code == 409
    elif operation == "claim":
        assert claim_commands(db, device.id, CAPS) == []
    else:
        dispatch.reconcile_commands(db, device.id)
    assert old.status == "expired" and dispatch.aware(old.completed_at) == current[0]
    assert old.superseded_by is None and old.attempt == 0
    assert sorted(db.scalars(select(AuditLog.action)).all()) == [
        "command_deadline_reconciled",
        "command_queued",
    ]


def test_explicit_clock_is_preserved_after_parent_wait(db, monkeypatch):
    import app.services.command_dispatch as dispatch

    device, user = seed(db)
    original = dispatch.lock_repeater
    current = [NOW]

    def waited(session, device_id):
        parent = original(session, device_id)
        current[0] = NOW + timedelta(days=2)
        return parent

    monkeypatch.setattr(dispatch, "lock_repeater", waited)
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    item = admit_command(db, request(device), user, now=NOW)
    assert item.status == "queued"
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    assert delivery.lease_expires_at == NOW + timedelta(seconds=60)


def test_result_implicit_authority_clock_after_parent_wait(db, monkeypatch):
    import app.services.command_dispatch as dispatch

    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.scalar(select(Certificate)).expires_at = NOW + timedelta(seconds=10)
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    current = [NOW]
    original = dispatch.lock_repeater

    def waited(session, device_id):
        parent = original(session, device_id)
        current[0] = NOW + timedelta(seconds=11)
        return parent

    monkeypatch.setattr(dispatch, "lock_repeater", waited)
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    with pytest.raises(HTTPException) as exc:
        accept_result(db, device.id, result(delivery, sent_at=NOW, completed_at=NOW))
    assert exc.value.status_code == 409
    assert exc.value.detail == "Current enrolled device authority required"
    assert item.result_json is None
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before


@pytest.mark.parametrize("scope", ["device", "global"])
def test_list_reconciliation_does_not_inject_selection_clock(db, monkeypatch, scope):
    import app.services.command_dispatch as dispatch
    from app.api.routes.commands import list_commands_v2

    device, user = seed(db)
    item = admit_command(db, request(device, expires_at=NOW + timedelta(seconds=10)), user, now=NOW)
    db.commit()
    current = [NOW]
    original = dispatch.lock_repeater

    def waited(session, device_id):
        parent = original(session, device_id)
        current[0] = NOW + timedelta(seconds=11)
        return parent

    monkeypatch.setattr(dispatch, "lock_repeater", waited)
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    monkeypatch.setattr("app.api.routes.commands.clock", lambda now: NOW)
    page = list_commands_v2(
        status_filter=None,
        device_id=device.id if scope == "device" else None,
        limit=1,
        offset=0,
        db=db,
        _=user,
    )
    assert page[0].command_id == item.id and page[0].status == "expired"
