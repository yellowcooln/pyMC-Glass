"""Parent-only real PostgreSQL reconciliation races, disposable schema fixture."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from app.db.models import AuditLog, DeviceCommand, DeviceCommandReceipt
from app.services.command_dispatch import (
    accept_result,
    claim_commands,
    reconcile_commands,
    reconcile_result,
)
from fastapi import HTTPException
from sqlalchemy import func, select
from test_command_claims_postgres import enqueue

from test_command_lifecycle import CAPS, NOW, result

pytest_plugins = ("test_command_claims_postgres",)


def offers(pg):
    factory, _, device_id, user_id, _ = pg
    command_id = enqueue(factory, device_id, user_id)
    with factory() as db:
        old = claim_commands(db, device_id, CAPS, now=NOW)[0]
        db.commit()
        current = claim_commands(db, device_id, CAPS, now=NOW + timedelta(seconds=61))[0]
        db.commit()
    return factory, device_id, command_id, old, current


def race(factory, device_id, values):
    barrier = Barrier(len(values))

    def submit(pair):
        service, offered = pair
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                ack = service(db, device_id, offered, now=NOW + timedelta(seconds=65))
                db.commit()
                return ack
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=len(values)) as pool:
        return list(pool.map(submit, values))


def test_archive_live_completion_serialized_no_authoritative_overwrite(pg):
    factory, device_id, command_id, old, current = offers(pg)
    historical = result(old, persisted=False, applied=False)
    live = result(
        current,
        sent_at=NOW + timedelta(seconds=63),
        completed_at=NOW + timedelta(seconds=63),
        persisted=True,
        applied=True,
    )
    acks = race(factory, device_id, [(reconcile_result, historical), (accept_result, live)])
    assert [a.disposition for a in acks] == ["superseded", "accepted"]
    with factory() as db:
        command = db.get(DeviceCommand, command_id)
        assert (
            command.status == "succeeded" and command.applied is True and command.persisted is True
        )
        assert command.acceptance_id == str(acks[1].acceptance_id)
        assert command.result_sha256 == acks[1].result_sha256
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 2
        assert (
            reconcile_result(db, device_id, historical, now=NOW + timedelta(seconds=66)) == acks[0]
        )


def test_duplicate_archive_same_uuid_digest_disposition_one_audit(pg):
    factory, device_id, command_id, old, _ = offers(pg)
    offered = result(old)
    acks = race(factory, device_id, [(reconcile_result, offered)] * 2)
    assert acks[0] == acks[1] and acks[0].disposition == "superseded"
    with factory() as db:
        assert db.get(DeviceCommand, command_id).result_json is None
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "command_result_archived")
            )
            == 1
        )


def test_archive_cap_race_last_slot_bounded_and_duplicate_retained(pg):
    factory, device_id, command_id, old, _ = offers(pg)
    first = result(old, "received", message="0")
    with factory() as db:
        ack = reconcile_result(db, device_id, first, now=NOW + timedelta(seconds=62))
        for index in range(1, 7):
            reconcile_result(
                db,
                device_id,
                result(old, "running", message=str(index)),
                now=NOW + timedelta(seconds=62),
            )
        db.commit()
    acks = race(
        factory,
        device_id,
        [(reconcile_result, result(old, "running", message=str(index))) for index in [7, 8]],
    )
    assert sum(a is not None for a in acks) == 1
    with factory() as db:
        command = db.get(DeviceCommand, command_id)
        assert command.result_json is None and command.status == "queued" and command.attempt == 2
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 8
        assert reconcile_result(db, device_id, first, now=NOW + timedelta(seconds=66)) == ack


@pytest.mark.parametrize("deadline_already_reconciled", [False, True])
def test_current_unknown_receipt_serialized_with_deadline_and_duplicate(
    pg, deadline_already_reconciled
):
    factory, _, device_id, user_id, _ = pg
    command_id = enqueue(factory, device_id, user_id, job=True)
    with factory() as db:
        delivery = claim_commands(db, device_id, CAPS, now=NOW)[0]
        if deadline_already_reconciled:
            reconcile_commands(db, device_id, now=NOW + timedelta(seconds=61))
        db.commit()
    offered = result(
        delivery,
        "unknown",
        sent_at=NOW + timedelta(seconds=62),
        error_code="execution_uncertain",
    )
    barrier = Barrier(3)

    def submit(service):
        with factory() as db:
            barrier.wait(timeout=5)
            if service is reconcile_commands:
                service(db, device_id, now=NOW + timedelta(seconds=65))
                ack = None
            else:
                ack = service(db, device_id, offered, now=NOW + timedelta(seconds=65))
            db.commit()
            return ack

    with ThreadPoolExecutor(max_workers=3) as pool:
        deadline, normal, reconciled = list(
            pool.map(submit, [reconcile_commands, accept_result, reconcile_result])
        )
    assert deadline is None and normal is not None and normal == reconciled
    assert normal.disposition == "accepted"
    with factory() as db:
        command = db.get(DeviceCommand, command_id)
        assert command.status == "unknown" and command.completed_at is None
        assert command.persisted is None and command.applied is None
        assert command.error_code == "execution_uncertain"
        assert command.acceptance_id == str(normal.acceptance_id)
        assert command.result_sha256 == normal.result_sha256
        assert db.scalar(select(func.count()).select_from(DeviceCommandReceipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "command_result_accepted")
            )
            == 1
        )
        audits = db.scalar(select(func.count()).select_from(AuditLog))
        for service in [accept_result, reconcile_result]:
            assert service(db, device_id, offered, now=NOW + timedelta(seconds=66)) == normal
        assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


def test_full_semicolon_migration_runner_actual_pg(pg):
    from uuid import uuid4

    from app.db.migrate import apply_migrations
    from sqlalchemy import create_engine, inspect, text

    _, engine, _, _, _ = pg
    schema = "glass_reconciliation_migration_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    migration_engine = create_engine(
        engine.url,
        connect_args={
            "options": f"-c search_path={schema} -c lock_timeout=10000 -c statement_timeout=15000"
        },
    )
    try:
        apply_migrations(migration_engine)
        apply_migrations(migration_engine)
        assert "device_command_leases" in inspect(migration_engine).get_table_names()
        with migration_engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM schema_migrations "
                        "WHERE version='0020_commandleasehistory'"
                    )
                )
                == 1
            )
    finally:
        migration_engine.dispose()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
