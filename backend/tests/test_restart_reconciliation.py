"""Task7 source-only reconciliation. SQLite does not prove row serialization."""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.contracts.v2.command import ResultAcceptanceV2, result_sha256
from app.contracts.v2.common import MAX_ENVELOPE_BYTES
from app.db.models import (
    AuditLog,
    DeviceCommand,
    DeviceCommandLease,
    DeviceCommandReceipt,
    DeviceCredential,
)
from app.services.command_dispatch import (
    accept_result,
    admit_command,
    claim_commands,
    reconcile_commands,
    reconcile_result,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from test_command_lifecycle import CAPS, NOW, request, result, seed

pytest_plugins = ("test_command_lifecycle",)


def test_late_current_read_result_resolves_uncertainty_after_request_deadline(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    late = result(delivery)
    expired_at = delivery.expires_at + timedelta(seconds=1)
    reconcile_commands(db, device.id, now=expired_at)
    db.commit()
    # Once an offer escaped, the deadline is uncertainty, not proof of expiry
    # without execution. A durable result from the original lease can resolve it.
    assert item.status == "unknown" and item.completed_at is None
    accepted = reconcile_result(db, device.id, late, now=expired_at)
    db.commit()
    assert accepted.disposition == "accepted"
    assert accepted.result_sha256 == result_sha256(late)
    assert item.status == "succeeded"
    after = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    assert reconcile_result(db, device.id, late, now=expired_at) == accepted
    assert snapshot(item) == after
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize("service", [accept_result, reconcile_result])
@pytest.mark.parametrize("prior", [None, "running", "awaiting_verification"])
def test_deadline_unknown_acknowledges_recovered_current_lease(db, service, prior):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    if prior is not None:
        progress = result(delivery, prior, persisted=True, restart_required=True)
        accept_result(db, device.id, progress, now=NOW + timedelta(seconds=3))
    reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
    db.commit()
    assert item.status == "unknown" and item.completed_at is None
    recovered = result(
        delivery,
        "unknown",
        sent_at=NOW + timedelta(seconds=62),
        error_code="execution_uncertain",
    )
    ack = service(db, device.id, recovered, now=NOW + timedelta(seconds=63))
    db.commit()
    assert ack.disposition == "accepted" and ack.result_sha256 == result_sha256(recovered)
    assert item.status == "unknown" and item.completed_at is None
    assert item.result_sha256 == ack.result_sha256
    assert item.acceptance_id == str(ack.acceptance_id)
    assert item.error_code == "execution_uncertain"
    assert item.persisted is (True if prior is not None else None)
    assert item.applied is None
    assert item.restart_required is (True if prior is not None else None)
    receipt = db.scalar(
        select(DeviceCommandReceipt).where(
            DeviceCommandReceipt.acceptance_id == str(ack.acceptance_id)
        )
    )
    assert receipt.result_json == item.result_json
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    for retry in [accept_result, reconcile_result, service]:
        assert retry(db, device.id, recovered, now=NOW + timedelta(seconds=64)) == ack
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == (2 if prior else 1)
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits
    # A fresh timestamp/body is not another phase of the same uncertainty.
    repeated = result(delivery, "unknown", sent_at=NOW + timedelta(seconds=64))
    with pytest.raises(HTTPException) as exc:
        service(db, device.id, repeated, now=NOW + timedelta(seconds=65))
    assert exc.value.status_code == 409
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == (2 if prior else 1)
    # Later original-lease evidence can still resolve the uncertainty.
    verified = result(
        delivery,
        "succeeded",
        sent_at=NOW + timedelta(seconds=66),
        completed_at=NOW + timedelta(seconds=66),
        applied=True,
    )
    service(db, device.id, verified, now=NOW + timedelta(seconds=67))
    assert item.status == "succeeded" and item.applied is True


@pytest.mark.parametrize("service", [accept_result, reconcile_result])
@pytest.mark.parametrize("bad", ["missing", "generation", "attempt", "issued", "expires"])
def test_recovered_unknown_requires_exact_current_history(db, service, bad):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
    lease = db.get(DeviceCommandLease, str(delivery.lease_id))
    if bad == "missing":
        db.delete(lease)
    elif bad == "generation":
        lease.credential_generation = "c" * 64
    elif bad == "attempt":
        lease.attempt += 1
    elif bad == "issued":
        lease.issued_at += timedelta(seconds=1)
    else:
        lease.expires_at += timedelta(seconds=1)
    db.commit()
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(HTTPException) as exc:
        service(db, device.id, result(delivery, "unknown"), now=NOW + timedelta(seconds=62))
    assert exc.value.status_code == 409
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize("service", [accept_result, reconcile_result])
def test_current_unknown_receipt_bound_keeps_exact_retry(db, service):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    for index, phase in enumerate(
        [
            "received",
            "running",
            "awaiting_verification",
            "unknown",
            "awaiting_verification",
            "unknown",
            "awaiting_verification",
            "unknown",
        ],
        start=1,
    ):
        offered = result(delivery, phase, sent_at=NOW + timedelta(seconds=index))
        ack = service(db, device.id, offered, now=NOW + timedelta(seconds=index))
    db.commit()
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    assert service(db, device.id, offered, now=NOW + timedelta(seconds=9)) == ack
    with pytest.raises(HTTPException) as exc:
        service(
            db,
            device.id,
            result(delivery, "awaiting_verification", sent_at=NOW + timedelta(seconds=9)),
            now=NOW + timedelta(seconds=9),
        )
    assert exc.value.status_code == 409 and "limit" in exc.value.detail
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 8
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize("path", ["current", "reconciled_current", "archive"])
def test_result_lock_statement_order(db, path):
    # Inspect emitted FOR UPDATE statements, not SQLite row-lock behavior.
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql

    if path == "archive":
        device, _, delivery, _ = reclaimed(db)
        service = reconcile_result
        offered = result(delivery)
    else:
        device, user = seed(db)
        admit_command(db, request(device, job=True), user, now=NOW)
        delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
        reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
        db.commit()
        service = accept_result if path == "current" else reconcile_result
        offered = result(delivery, "unknown")
    locked = []

    def record(execution):
        statement = execution.statement
        if getattr(statement, "_for_update_arg", None) is not None:
            assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
            locked.extend(column["entity"] for column in statement.column_descriptions)

    event.listen(db, "do_orm_execute", record)
    try:
        service(db, device.id, offered, now=NOW + timedelta(seconds=62))
    finally:
        event.remove(db, "do_orm_execute", record)
    assert locked[0] is type(device)
    assert locked.index(DeviceCommand) < locked.index(DeviceCommandLease)
    first_receipt = locked.index(DeviceCommandReceipt)
    assert all(
        index < first_receipt for index, entity in enumerate(locked) if entity is DeviceCommandLease
    )


@pytest.mark.parametrize("bad", ["missing", "metadata", "generation"])
def test_exact_unknown_ack_survives_missing_or_conflicting_history(db, bad):
    device, user = seed(db)
    item = admit_command(db, request(device, job=True), user, now=NOW)
    delivery = claim_commands(db, device.id, CAPS, now=NOW)[0]
    reconcile_commands(db, device.id, now=NOW + timedelta(seconds=61))
    offered = result(delivery, "unknown")
    ack = accept_result(db, device.id, offered, now=NOW + timedelta(seconds=62))
    lease = db.get(DeviceCommandLease, str(delivery.lease_id))
    if bad == "missing":
        db.delete(lease)
    elif bad == "metadata":
        item.lease_issued_at = None
        item.lease_expires_at = None
    else:
        lease.credential_generation = "c" * 64
    db.commit()
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    assert accept_result(db, device.id, offered, now=NOW + timedelta(seconds=63)) == ack
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


def reclaimed(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    first = claim_commands(db, device.id, CAPS, now=NOW)[0]
    db.commit()
    second = claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=61))[0]
    db.commit()
    return device, item, first, second


def snapshot(item):
    from app.services.command_dispatch import aware

    return {
        c.name: aware(getattr(item, c.name))
        if isinstance(getattr(item, c.name), datetime)
        else getattr(item, c.name)
        for c in DeviceCommand.__table__.columns
    }


def test_archive_exact_old_result_non_authoritative_and_retained_ack(db):
    device, item, old, current = reclaimed(db)
    offered = result(old, persisted=True, applied=True, restart_required=True)
    before = snapshot(item)
    with pytest.raises(HTTPException) as exc:
        accept_result(db, device.id, offered, now=NOW + timedelta(seconds=62))
    assert exc.value.status_code == 409
    ack = reconcile_result(db, device.id, offered, now=NOW + timedelta(seconds=62))
    db.commit()
    assert ack.disposition == "superseded" and ack.result_sha256 == result_sha256(offered)
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandLease)) == 2
    live = result(
        current, sent_at=NOW + timedelta(seconds=63), completed_at=NOW + timedelta(seconds=63)
    )
    live_ack = reconcile_result(db, device.id, live, now=NOW + timedelta(seconds=64))
    db.commit()
    assert live_ack.disposition == "accepted" and item.status == "succeeded"
    final = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    assert reconcile_result(db, device.id, offered, now=NOW + timedelta(seconds=65)) == ack
    assert accept_result(db, device.id, offered, now=NOW + timedelta(seconds=65)) == ack
    assert snapshot(item) == final
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


@pytest.mark.parametrize(
    "bad",
    [
        "missing_old",
        "missing_current",
        "generation",
        "revoked",
        "lease",
        "attempt",
        "execution",
        "device",
        "request",
        "early",
        "future",
        "history_generation",
    ],
)
def test_archive_fail_closed_without_receipt_or_audit(db, bad):
    device, item, old, current = reclaimed(db)
    changes = {}
    if bad.startswith("missing_"):
        db.delete(
            db.get(
                DeviceCommandLease, str(old.lease_id if bad == "missing_old" else current.lease_id)
            )
        )
    elif bad == "generation":
        db.get(DeviceCredential, device.id).token_hash = "c" * 64
    elif bad == "revoked":
        db.get(DeviceCredential, device.id).revoked_at = NOW
    elif bad == "history_generation":
        db.get(DeviceCommandLease, str(old.lease_id)).credential_generation = "c" * 64
    elif bad in {"lease", "execution", "device", "request"}:
        changes[bad + "_id"] = str(uuid4())
    elif bad == "attempt":
        changes["attempt"] = 2
    else:
        changes["sent_at"] = changes["completed_at"] = NOW + timedelta(
            seconds=-1 if bad == "early" else 70
        )
    db.commit()
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(HTTPException) as exc:
        reconcile_result(db, device.id, result(old, **changes), now=NOW + timedelta(seconds=62))
    assert exc.value.status_code == 409
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


def test_prior_accepted_receipt_keeps_original_disposition_after_reclaim(db):
    device, user = seed(db)
    item = admit_command(db, request(device), user, now=NOW)
    old = claim_commands(db, device.id, CAPS, now=NOW)[0]
    offered = result(old, "received")
    ack = accept_result(db, device.id, offered, now=NOW + timedelta(seconds=3))
    db.commit()
    claim_commands(db, device.id, CAPS, now=NOW + timedelta(seconds=61))
    db.commit()
    before = snapshot(item)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    assert reconcile_result(db, device.id, offered, now=NOW + timedelta(seconds=62)) == ack
    assert ack.disposition == "accepted" and snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


def test_archive_cap_duplicate_still_ack_no_authoritative_mutation(db):
    device, item, old, _ = reclaimed(db)
    before = snapshot(item)
    first = result(old, "received", message="0")
    ack = reconcile_result(db, device.id, first, now=NOW + timedelta(seconds=62))
    for index in range(1, 8):
        reconcile_result(
            db,
            device.id,
            result(old, "running", message=str(index)),
            now=NOW + timedelta(seconds=62),
        )
    db.commit()
    assert reconcile_result(db, device.id, first, now=NOW + timedelta(seconds=63)) == ack
    with pytest.raises(HTTPException):
        reconcile_result(
            db, device.id, result(old, message="overflow"), now=NOW + timedelta(seconds=63)
        )
    assert snapshot(item) == before
    assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 8


def test_gate_before_session_and_auth(monkeypatch):
    from app.api.routes import device_reconciliation as route

    app = FastAPI()
    app.include_router(route.router)
    calls = []

    def forbidden_db():
        calls.append("db")
        raise AssertionError("DB must not run before gate")
        yield

    monkeypatch.setattr(route, "get_db_session", forbidden_db)
    with TestClient(app, base_url="https://testserver") as client:
        assert (
            client.post(
                "/device/commands/results/reconcile", content=b"x" * (MAX_ENVELOPE_BYTES + 1)
            ).status_code
            == 413
        )
        assert client.post("/device/commands/results/reconcile", content=b"{}").status_code == 422
        assert (
            client.post(
                "http://testserver/device/commands/results/reconcile", content=b"{}"
            ).status_code
            == 400
        )
    assert calls == []


def test_route_auth_exact_archive_current_result_and_revocation(monkeypatch):
    from app.api.routes import device_reconciliation as route
    from app.db.base import Base
    from app.security.tokens import hash_token
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(route.router)
    monkeypatch.setattr(
        "app.services.command_dispatch.clock",
        lambda now: NOW + timedelta(seconds=65) if now is None else now,
    )
    with Session(engine, expire_on_commit=False) as session:
        device, user = seed(session)
        token = "d" * 43
        session.get(DeviceCredential, device.id).token_hash = hash_token(token)
        session.commit()
        item = admit_command(session, request(device), user, now=NOW)
        old = claim_commands(session, device.id, CAPS, now=NOW)[0]
        session.commit()
        current = claim_commands(session, device.id, CAPS, now=NOW + timedelta(seconds=61))[0]
        session.commit()

        def gated_session():
            yield session

        monkeypatch.setattr(route, "get_db_session", gated_session)
        offered = result(old).model_dump(mode="json")
        with TestClient(app, base_url="https://testserver") as client:
            url = "/device/commands/results/reconcile"
            assert client.post(url, json=offered).status_code == 401
            client.headers["Authorization"] = "Bearer " + token
            archived = client.post(url, json=offered)
            assert archived.status_code == 200, archived.text
            assert archived.headers["Cache-Control"] == "no-store"
            assert archived.json()["disposition"] == "superseded"
            assert session.get(DeviceCommand, item.id).result_json is None
            assert client.post(url, json=offered).json() == archived.json()
            live = result(
                current,
                sent_at=NOW + timedelta(seconds=63),
                completed_at=NOW + timedelta(seconds=63),
            ).model_dump(mode="json")
            accepted = client.post(url, json=live)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["disposition"] == "accepted"
            assert session.get(DeviceCommand, item.id).status == "succeeded"
            session.get(DeviceCredential, device.id).revoked_at = NOW
            session.commit()
            assert client.post(url, json=offered).status_code == 401
    engine.dispose()


def test_migration_backfills_only_trusted_current_offer(db):
    device, item, old, current = reclaimed(db)
    db.execute(text("DROP TABLE device_command_leases"))
    db.commit()
    sql = (Path(__file__).parents[1] / "app/db/migrations/0020_commandleasehistory.sql").read_text()
    # Existing receipt model already has disposition; exercise CREATE and INSERT
    # on populated v19 columns, the full semicolon runner is tested below.
    for statement in sql.split(";"):
        if statement.strip() and not statement.strip().startswith("ALTER TABLE"):
            db.execute(text(statement))
    db.commit()
    leases = db.scalars(select(DeviceCommandLease)).all()
    assert len(leases) == 1 and leases[0].id == str(current.lease_id)
    with pytest.raises(HTTPException):
        reconcile_result(db, device.id, result(old), now=NOW + timedelta(seconds=62))


def test_full_migration_runner_and_check():
    from app.db.migrate import apply_migrations
    from sqlalchemy import create_engine, inspect

    engine = create_engine("sqlite://")
    apply_migrations(engine)
    apply_migrations(engine)
    assert "device_command_leases" in inspect(engine).get_table_names()
    assert "disposition" in {
        c["name"] for c in inspect(engine).get_columns("device_command_receipts")
    }
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM schema_migrations "
                    "WHERE version='0020_commandleasehistory'"
                )
            )
            == 1
        )
    engine.dispose()


def test_paired_acceptance_optional_disposition_and_digest(db):
    # Load the paired stdlib contract directly, no node handler/store dependency.
    import os
    import sys

    sibling = Path(__file__).parents[3] / "repeater-glass-revamp/repeater/glass/contracts.py"
    path = Path(os.environ.get("GLASS_NODE_CONTRACTS_PATH", str(sibling)))
    if not path.is_file():
        pytest.skip("Paired node checkout required; set GLASS_NODE_CONTRACTS_PATH")
    spec = importlib.util.spec_from_file_location("task7_node_contracts", path)
    assert spec is not None and spec.loader is not None
    node = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = node
    spec.loader.exec_module(node)
    device, _, old, _ = reclaimed(db)
    offered = result(old)
    ack = reconcile_result(db, device.id, offered, now=NOW + timedelta(seconds=62))
    parsed = node.ResultAcceptanceV2.model_validate(ack.model_dump(mode="json"))
    assert parsed.disposition == "superseded"
    inform_raw = dict(
        version=2,
        type="inform",
        device_id=device.id,
        boot_id=str(uuid4()),
        sent_at=NOW + timedelta(seconds=62),
        node_name="synthetic",
        software_version="test",
        capabilities=CAPS,
        inventory={"radios": [], "identities": [], "sensors": [], "plugins": []},
        telemetry={},
        results=[offered.model_dump(mode="json")],
    )
    response_raw = dict(
        version=2,
        type="response",
        device_id=device.id,
        boot_id=inform_raw["boot_id"],
        sent_at=inform_raw["sent_at"],
        interval_seconds=30,
        accepted_results=[ack.model_dump(mode="json")],
        queries=[],
        jobs=[],
    )
    node.ResponseV2.model_validate(response_raw).check_inform(
        node.InformV2.model_validate(inform_raw)
    )
    response_raw["accepted_results"][0]["result_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        node.ResponseV2.model_validate(response_raw).check_inform(
            node.InformV2.model_validate(inform_raw)
        )
    assert (
        node.result_sha256(node.ResultV2.model_validate(offered.model_dump(mode="json")))
        == ack.result_sha256
    )
    raw = ack.model_dump(mode="json")
    del raw["disposition"]
    assert node.ResultAcceptanceV2.model_validate(raw).disposition == "accepted"
    assert ResultAcceptanceV2.model_validate(raw).disposition == "accepted"
    for invalid in [None, "other", True]:
        with pytest.raises(ValueError):
            node.ResultAcceptanceV2.model_validate({**raw, "disposition": invalid})
        with pytest.raises(ValueError):
            ResultAcceptanceV2.model_validate({**raw, "disposition": invalid})
