"""Opt-in actual PostgreSQL Task6 serialization proof, never SQLite emulation.

Parent runs GLASS_COMMANDS_PG_TEST=1 DATABASE_URL=postgresql+psycopg://...
in its isolated lab. A unique disposable schema is the only database scope.
No application startup, enrollment crypto, runtime node execution or live access.
"""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from app.db.base import Base
from app.db.models import (
    AuditLog,
    DeviceCommand,
    DeviceCommandReceipt,
    DeviceObservation,
    Repeater,
    User,
)
from app.security.devices import lock_repeater
from app.services.command_dispatch import accept_result, admit_command, claim_commands
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from test_device_auth_concurrency import wait_for_database_lock

from test_command_lifecycle import CAPS, NOW, request, result, seed


@pytest.fixture
def pg(monkeypatch):
    if os.environ.get("GLASS_COMMANDS_PG_TEST") != "1":
        pytest.skip(
            "Parent-only: GLASS_COMMANDS_PG_TEST=1 and isolated PostgreSQL DATABASE_URL required"
        )
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("Explicit isolated PostgreSQL required; SQLite is not a locking proof")
    schema = "glass_commands_test_" + uuid4().hex
    engine = create_engine(
        url, connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=15000"}
    )
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        Base.metadata.create_all(scoped)
        factory = sessionmaker(bind=scoped, autoflush=False, expire_on_commit=False)
        with factory() as db:
            device, user = seed(db)
            device_id, user_id = device.id, user.id
        module = importlib.import_module("app.api.routes.inform_v2")

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW + timedelta(seconds=5)

        monkeypatch.setattr(module, "datetime", FixedDatetime)
        yield factory, engine, device_id, user_id, module
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def enqueue(factory, device_id, user_id, job=False):
    with factory() as db:
        item = admit_command(
            db, request(db.get(Repeater, device_id), job=job), db.get(User, user_id), now=NOW
        )
        db.commit()
        return item.id


def raw_inform(device_id, results=()):
    return dict(
        version=2,
        type="inform",
        device_id=device_id,
        boot_id=str(uuid4()),
        sent_at=NOW + timedelta(seconds=5),
        node_name="synthetic",
        software_version="offline-test",
        capabilities=CAPS,
        inventory={"radios": [], "identities": [], "sensors": [], "plugins": []},
        telemetry={},
        results=[r.model_dump(mode="json") for r in results],
    )


def poll(factory, module, device_id, raw=None):
    with factory() as db:
        return module.inform_v2(
            raw=raw or raw_inform(device_id), device=lock_repeater(db, device_id), db=db
        )


def test_concurrent_informs_only_one_delivery_actual_pg(pg):
    factory, engine, device_id, user_id, module = pg
    command_id = enqueue(factory, device_id, user_id)
    ready = Event()
    state = {}

    def waiter():
        with factory() as db:
            state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            ready.set()
            return module.inform_v2(
                raw=raw_inform(device_id), device=lock_repeater(db, device_id), db=db
            )

    with factory() as first, ThreadPoolExecutor(max_workers=1) as pool:
        lock_repeater(first, device_id)
        offered = claim_commands(first, device_id, CAPS, now=NOW)
        future = pool.submit(waiter)
        assert ready.wait(5)
        wait_for_database_lock(engine, state["pid"])
        first.commit()
        second = future.result(timeout=15)
    assert len(offered) == 1 and second.queries == () and second.jobs == ()
    with factory() as db:
        assert db.get(DeviceCommand, command_id).attempt == 1
        assert db.get(DeviceCommand, command_id).lease_id == str(offered[0].lease_id)


def test_concurrent_exact_admission_is_one_command_one_audit(pg):
    factory, _, device_id, user_id, _ = pg
    with factory() as db:
        req = request(db.get(Repeater, device_id))
    barrier = Barrier(2)

    def submit():
        with factory() as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=5)
            item = admit_command(db, req, user, now=NOW)
            db.commit()
            return item.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        ids = [f.result(timeout=15) for f in futures]
    assert ids[0] == ids[1]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "command_queued")
            )
            == 1
        )


def test_concurrent_result_and_lost_ack_same_acceptance_no_audit_dup(pg):
    factory, _, device_id, user_id, module = pg
    command_id = enqueue(factory, device_id, user_id)
    with factory() as db:
        delivery = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.commit()
    offered = result(delivery)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return poll(factory, module, device_id, raw_inform(device_id, [offered]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        responses = [f.result(timeout=15) for f in futures]
    assert responses[0].accepted_results == responses[1].accepted_results
    retry = poll(factory, module, device_id, raw_inform(device_id, [offered]))
    assert retry.accepted_results == responses[0].accepted_results
    with factory() as db:
        assert db.get(DeviceCommand, command_id).status == "succeeded"
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "command_result_accepted")
            )
            == 1
        )


def test_safe_query_lost_delivery_reclaim_stale_result_and_no_stranding(pg):
    factory, _, device_id, user_id, module = pg
    command_id = enqueue(factory, device_id, user_id)
    with factory() as db:
        first = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.commit()
    with factory() as db:
        assert claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=59)) == []
        second = claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=61))[0]
        db.commit()
    assert second.attempt == 2 and second.lease_id != first.lease_id
    with factory() as db:
        prior = db.get(DeviceObservation, device_id).boot_id
    with pytest.raises(HTTPException):
        poll(factory, module, device_id, raw_inform(device_id, [result(first)]))
    with factory() as db:
        assert db.get(DeviceObservation, device_id).boot_id == prior
        assert db.get(DeviceCommand, command_id).attempt == 2
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0
        db.get(DeviceObservation, device_id).received_at = NOW + timedelta(seconds=122)
        db.commit()
        third = claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=122))[0]
        db.commit()
        assert third.attempt == 3
    with factory() as db:
        assert claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=183)) == []
        db.commit()
        assert db.get(DeviceCommand, command_id).status == "unknown"


def test_job_lost_delivery_unknown_and_exact_original_lease_resolution(pg):
    factory, _, device_id, user_id, _ = pg
    command_id = enqueue(factory, device_id, user_id, job=True)
    with factory() as db:
        delivery = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.commit()
    with factory() as db:
        assert claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=61)) == []
        db.commit()
        assert db.get(DeviceCommand, command_id).status == "unknown"
        assert db.get(DeviceCommand, command_id).completed_at is None
    with factory() as db:
        offered = result(delivery)
        ack = accept_result(db, device_id, offered, now=NOW + timedelta(seconds=62))
        db.commit()
    with factory() as db:
        assert db.get(DeviceCommand, command_id).status == "succeeded"
        assert accept_result(db, device_id, offered, now=NOW + timedelta(seconds=63)) == ack
        with pytest.raises(HTTPException):
            accept_result(
                db,
                device_id,
                result(delivery, boot_id=str(uuid4())),
                now=NOW + timedelta(seconds=63),
            )


def test_late_reactivation_competes_with_admission_for_last_slot_actual_pg(pg):
    from app.services.command_dispatch import ACTIVE

    from test_command_lifecycle import bulk_commands

    factory, _, device_id, user_id, _ = pg
    command_id = enqueue(factory, device_id, user_id, job=True)
    with factory() as db:
        delivery = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.commit()
        # Executor uncertainty can arrive before lease expiry; a reactivated
        # command must retain its slot while admission contends for that slot.
        accept_result(db, device_id, result(delivery, "unknown"), now=NOW + timedelta(seconds=3))
        db.commit()
        bulk_commands(db, db.get(Repeater, device_id), db.get(User, user_id), 255)
        audit_before = db.scalar(select(func.count()).select_from(AuditLog))
    late = result(delivery, "awaiting_verification", sent_at=NOW + timedelta(seconds=4))
    barrier = Barrier(2)

    def submit(reactivate):
        with factory() as db:
            device, user = db.get(Repeater, device_id), db.get(User, user_id)
            barrier.wait(timeout=5)
            try:
                if reactivate:
                    accept_result(db, device_id, late, now=NOW + timedelta(seconds=5))
                else:
                    admit_command(db, request(device), user, now=NOW + timedelta(seconds=5))
                db.commit()
                return True
            except HTTPException as exc:
                assert exc.status_code == 409
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, reactivate) for reactivate in (True, False)]
        outcomes = [future.result(timeout=15) for future in futures]
    assert sorted(outcomes) == [False, True]
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DeviceCommand)
                .where(DeviceCommand.status.in_(ACTIVE))
            )
            == 256
        )
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1 + int(
            outcomes[0]
        )
        assert db.get(DeviceCommand, command_id).status == (
            "awaiting_verification" if outcomes[0] else "unknown"
        )
        assert db.scalar(select(func.count()).select_from(AuditLog)) == audit_before + 1


def run_clock_jump_waiter(pg, monkeypatch, invoke, after):
    """Advance production clock only after observing a real PostgreSQL lock wait."""
    import app.services.command_dispatch as dispatch

    factory, engine, device_id, _, _ = pg
    current = [NOW]
    monkeypatch.setattr(dispatch, "clock", lambda now: current[0] if now is None else now)
    ready = Event()
    state = {}

    def waiter():
        with factory() as db:
            state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            ready.set()
            try:
                value = invoke(db)
            except HTTPException as exc:
                # Preserve only lazy deadline reconciliation for inspection.
                # The API normally rolls back rejected transactions.
                db.commit()
                return exc
            db.commit()
            return value

    with factory() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        lock_repeater(blocker, device_id)
        future = pool.submit(waiter)
        assert ready.wait(5)
        wait_for_database_lock(engine, state["pid"])
        current[0] = after
        blocker.commit()
        return future.result(timeout=15)


@pytest.mark.parametrize("boundary", ["request", "certificate", "capabilities"])
def test_production_admission_clock_after_actual_pg_wait(pg, monkeypatch, boundary):
    from app.db.models import Certificate

    factory, _, device_id, user_id, _ = pg
    with factory() as db:
        req = request(
            db.get(Repeater, device_id),
            expires_at=NOW + timedelta(seconds=10 if boundary == "request" else 3600),
        )
        if boundary == "certificate":
            db.scalar(select(Certificate)).expires_at = NOW + timedelta(seconds=10)
        db.commit()
    after = NOW + timedelta(seconds=121 if boundary == "capabilities" else 11)
    outcome = run_clock_jump_waiter(
        pg,
        monkeypatch,
        lambda db: admit_command(db, req, db.get(User, user_id), now=None),
        after,
    )
    assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 0
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize("operation", ["cancel", "supersede", "claim", "reconcile"])
def test_queued_expiry_crossing_actual_pg_wait(pg, monkeypatch, operation):
    from app.services.command_dispatch import cancel_command, reconcile_commands

    factory, _, device_id, user_id, _ = pg
    with factory() as db:
        device = db.get(Repeater, device_id)
        old = admit_command(
            db,
            request(device, expires_at=NOW + timedelta(seconds=10)),
            db.get(User, user_id),
            now=NOW,
        )
        old_id = old.id
        replacement = request(device)
        db.commit()

    def invoke(db):
        if operation == "cancel":
            return cancel_command(db, old_id, db.get(User, user_id), now=None)
        if operation == "supersede":
            return admit_command(
                db, replacement, db.get(User, user_id), now=None, supersedes_command_id=old_id
            )
        if operation == "claim":
            return claim_commands(db, device_id, CAPS, now=None)
        return reconcile_commands(db, device_id, now=None)

    after = NOW + timedelta(seconds=11)
    outcome = run_clock_jump_waiter(pg, monkeypatch, invoke, after)
    if operation in {"cancel", "supersede"}:
        assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    elif operation == "claim":
        assert outcome == []
    with factory() as db:
        old = db.get(DeviceCommand, old_id)
        assert old.status == "expired" and old.completed_at == after
        assert old.superseded_by is None and old.attempt == 0 and old.lease_id is None
        assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 1
        assert sorted(db.scalars(select(AuditLog.action)).all()) == [
            "command_deadline_reconciled",
            "command_queued",
        ]


def test_result_authority_clock_after_actual_pg_wait(pg, monkeypatch):
    from app.db.models import Certificate

    factory, _, device_id, user_id, _ = pg
    command_id = enqueue(factory, device_id, user_id)
    with factory() as db:
        delivery = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.scalar(select(Certificate)).expires_at = NOW + timedelta(seconds=10)
        db.commit()
        before = db.scalar(select(func.count()).select_from(AuditLog))
    outcome = run_clock_jump_waiter(
        pg,
        monkeypatch,
        lambda db: accept_result(
            db, device_id, result(delivery, sent_at=NOW, completed_at=NOW), now=None
        ),
        NOW + timedelta(seconds=11),
    )
    assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    assert outcome.detail == "Current enrolled device authority required"
    with factory() as db:
        assert db.get(DeviceCommand, command_id).result_json is None
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 0
        assert db.scalar(select(func.count()).select_from(AuditLog)) == before
