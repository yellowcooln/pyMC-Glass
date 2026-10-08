"""Opt-in PostgreSQL row-lock proof, using only a unique disposable schema.

GLASS_DEVICE_AUTH_PG_TEST=1 DATABASE_URL=postgresql+psycopg://.../isolated_test_db \
  .venv/bin/python -m pytest tests/integration/test_device_auth_concurrency.py

No application startup, live credentials, public-table writes, or production deletes.
"""

import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from app.api.routes.inform import inform
from app.api.routes.inform_v2 import inform_v2
from app.db.base import Base
from app.db.models import CommandQueueItem, DeviceCredential, DeviceEnrollment, Repeater
from app.security.devices import get_current_device, lock_repeater, revoke_device_credentials
from app.security.tokens import hash_token
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from test_device_auth import legacy, v2


@pytest.fixture
def pg():
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("GLASS_DEVICE_AUTH_PG_TEST") != "1":
        pytest.skip("Set GLASS_DEVICE_AUTH_PG_TEST=1 and DATABASE_URL to an isolated PostgreSQL DB")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("Explicit PostgreSQL DATABASE_URL required; SQLite cannot prove row locks")
    schema = "glass_auth_test_" + uuid4().hex
    engine = create_engine(
        url, connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=15000"}
    )
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        Base.metadata.create_all(scoped)
        factory = sessionmaker(bind=scoped, autoflush=False, expire_on_commit=False)
        device_id, token = str(uuid4()), secrets.token_urlsafe(32)
        with factory() as db:
            db.add(Repeater(id=device_id, node_name="node-one", pubkey="ab" * 32, status="adopted"))
            db.flush()
            db.add(DeviceCredential(
                repeater_id=device_id, token_hash=hash_token(token),
                csr_public_key_sha256="0" * 64, cert_serial="synthetic",
            ))
            db.add(DeviceEnrollment(
                repeater_id=device_id, token_hash=hash_token(secrets.token_urlsafe(32)),
                expected_node_name="node-one", expected_pubkey="ab" * 32,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            ))
            item = CommandQueueItem(
                repeater_id=device_id, command="restart_service", status="queued"
            )
            db.add(item)
            db.commit()
            command_id = item.id
        request = Request({
            "type": "http", "scheme": "https", "server": ("testserver", 443),
            "path": "/inform", "headers": [(b"authorization", ("Bearer " + token).encode())],
        })
        credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        yield factory, engine, device_id, command_id, request, credential
    finally:
        # Only our cryptographically unique schema is dropped, never public/application tables.
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def wait_for_database_lock(engine, pid):
    """Prove a DB wait, not a scheduler delay or Python mutex."""
    deadline = time.monotonic() + 5
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            waiting = observer.scalar(text(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
            ), {"pid": pid})
            observer.commit()
            if waiting == "Lock":
                return
            time.sleep(0.02)
    pytest.fail("Competing transaction did not block on a PostgreSQL lock")


def mutate(db, device_id, mutation):
    device = lock_repeater(db, device_id)
    if mutation == "reject":
        device.status = "rejected"
    revoke_device_credentials(db, device_id)


def operate(db, device_id, request, credential, route):
    if route == "legacy":
        return inform(request=request, raw=legacy(), db=db, credentials=credential)
    device = get_current_device(request, credential, db)
    return inform_v2(raw=v2(device_id), device=device, db=db)


@pytest.mark.parametrize("route", ["legacy", "v2"])
@pytest.mark.parametrize("mutation", ["revoke", "reject"])
def test_admin_commit_before_auth_lock_denies_stale_session(pg, route, mutation):
    factory, engine, device_id, command_id, request, credential = pg
    ready, start = Event(), Event()
    state = {}

    def operation():
        with factory() as db:
            # Keep stale objects loaded before the winning admin transaction.
            device = db.get(Repeater, device_id)
            old_credential = db.get(DeviceCredential, device_id)
            state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            ready.set()
            assert start.wait(5)
            assert device.status == "adopted" and old_credential.revoked_at is None
            with pytest.raises(HTTPException) as denied:
                operate(db, device_id, request, credential, route)
            assert denied.value.status_code == 401
            db.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(operation)
        assert ready.wait(5)
        with factory() as admin:
            try:
                mutate(admin, device_id, mutation)
                start.set()
                wait_for_database_lock(engine, state["pid"])
                admin.commit()
            finally:
                start.set()
                admin.rollback()
        future.result(timeout=15)
    with factory() as db:
        assert db.get(CommandQueueItem, command_id).status == "queued"
        assert db.get(Repeater, device_id).last_inform_at is None
        assert db.get(DeviceCredential, device_id).revoked_at is not None
        assert db.get(DeviceEnrollment, device_id).consumed_at is not None
        assert db.get(Repeater, device_id).status == (
            "rejected" if mutation == "reject" else "adopted"
        )


@pytest.mark.parametrize("route", ["legacy", "v2"])
@pytest.mark.parametrize("mutation", ["revoke", "reject"])
def test_authenticated_operation_commits_before_waiting_admin(pg, route, mutation):
    factory, engine, device_id, _, request, credential = pg
    ready, start = Event(), Event()
    state = {}

    def administration():
        with factory() as db:
            state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            ready.set()
            assert start.wait(5)
            mutate(db, device_id, mutation)
            db.commit()

    with factory() as operation_db, ThreadPoolExecutor(max_workers=1) as pool:
        try:
            # Authority remains locked through actual observation/dispatch route commit.
            get_current_device(request, credential, operation_db)
            future = pool.submit(administration)
            assert ready.wait(5)
            start.set()
            wait_for_database_lock(engine, state["pid"])
            operate(operation_db, device_id, request, credential, route)
        finally:
            start.set()
            operation_db.rollback()
        future.result(timeout=15)
    with factory() as db:
        assert db.get(Repeater, device_id).status == (
            "rejected" if mutation == "reject"
            else ("connected" if route == "legacy" else "adopted")
        )
        with pytest.raises(HTTPException) as denied:
            get_current_device(request, credential, db)
        assert denied.value.status_code == 401
