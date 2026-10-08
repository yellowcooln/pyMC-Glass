"""Opt-in PostgreSQL renewal/revocation barriers, never a live node or broker.

From backend, with DATABASE_URL already set to an isolated PostgreSQL test DB:
  GLASS_DEVICE_AUTH_PG_TEST=1 .venv/bin/python -m pytest -q \
    tests/integration/test_device_certificate_concurrency.py

Reuse the auth suite's unique disposable schema, synthetic bearer/enrollment,
server-side lock/statement timeouts, and schema-only cleanup. No app startup.
These call the real locked dependency and renewal handler after body validation;
they do not replace the separate ASGI body/dependency-order tests.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from uuid import uuid4

import pytest
from app.api.routes import device_certificates as routes
from app.config import Settings
from app.db.models import (
    AuditLog,
    Certificate,
    DeviceCertificateRotation,
    DeviceCredential,
    DeviceEnrollment,
    Repeater,
)
from app.schemas.device_certificates import CertificateRenewalRequest
from app.security.devices import get_current_device, revoke_device_credentials
from app.services.pki import PkiService
from fastapi import HTTPException, Response
from sqlalchemy import func, select, text
from test_device_auth_concurrency import pg as pg_auth  # noqa: F401 -- shared opt-in fixture

from test_device_certificate_rotation import make_csr


@pytest.fixture
def renewal(pg_auth, tmp_path, monkeypatch):  # noqa: F811 -- pytest injects imported fixture
    factory, engine, device_id, _, request, bearer = pg_auth
    settings = Settings(_env_file=None, pki_state_dir=str(tmp_path / "pki"))
    # Generate the disposable CA before any row-lock barrier is entered.
    PkiService(settings).ensure_ca()
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    with factory() as db:
        db.get(Repeater, device_id).cert_serial = "synthetic"
        db.add(
            Certificate(
                repeater_id=device_id,
                serial="synthetic",
                cn="device:" + device_id,
                issued_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=1),
                pem_hash="b" * 64,
            )
        )
        db.commit()
    csr, _ = make_csr()
    payload = CertificateRenewalRequest(
        device_id=device_id,
        request_id=str(uuid4()),
        csr_pem=csr,
    )
    issued, counter_lock = [], Lock()
    original = PkiService.issue_device_certificate

    def counted(service, **kwargs):
        bundle = original(service, **kwargs)
        with counter_lock:
            issued.append(bundle.serial)
        return bundle

    monkeypatch.setattr(PkiService, "issue_device_certificate", counted)
    return factory, engine, device_id, request, bearer, payload, issued


def renew(db, request, bearer, payload):
    # Exercise the route-specific locked authority dependency, not a fake device.
    device = routes.renewal_device(request=request, db=db, credentials=bearer)
    response = Response()
    public = routes.renew_certificate(response=response, device=device, payload=payload, db=db)
    assert response.headers["cache-control"] == "no-store"
    return public.model_dump(mode="json")


def submit(pool, factory, operation, *, stale_device_id=None):
    """Publish the exact worker backend; rollback even when a barrier/assertion fails."""
    ready, state = Event(), {}

    def worker():
        with factory() as db:
            try:
                if stale_device_id is not None:
                    # Retain these objects so populate_existing must refresh them.
                    state["stale_node"] = db.get(Repeater, stale_device_id)
                    state["stale_credential"] = db.get(DeviceCredential, stale_device_id)
                    assert state["stale_credential"].revoked_at is None
                state["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                ready.set()
                return operation(db)
            finally:
                ready.set()
                db.rollback()

    future = pool.submit(worker)
    assert ready.wait(5), "Worker did not reach its bounded transaction barrier"
    if "pid" not in state:
        future.result(timeout=20)
        pytest.fail("Worker did not publish a PostgreSQL backend")
    return future, state


def wait_for_blocker(engine, waiter, blocker):
    """Require a PostgreSQL lock wait on THIS transaction, not a guessed sleep."""
    deadline = time.monotonic() + 5
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            blocked = observer.scalar(
                text(
                    "SELECT wait_event_type = 'Lock' AND :blocker = ANY(pg_blocking_pids(pid)) "
                    "FROM pg_stat_activity WHERE pid = :waiter"
                ),
                {"waiter": waiter, "blocker": blocker},
            )
            observer.commit()  # Avoid a cached pg_stat_activity snapshot.
            if blocked:
                return
            time.sleep(0.02)  # Poll observation only; never establishes ordering.
    pytest.fail("Competing transaction did not wait on the expected PostgreSQL backend")


def pause_issuance(monkeypatch):
    """Pause inside the actual handler while its authenticated DB locks are held."""
    entered, release = Event(), Event()
    original = PkiService.issue_device_certificate

    def paused(service, **kwargs):
        entered.set()
        assert release.wait(10), "Issuance barrier was not released"
        return original(service, **kwargs)

    monkeypatch.setattr(PkiService, "issue_device_certificate", paused)
    return entered, release


def assert_issuance(db, device_id, public):
    rotation = db.get(DeviceCertificateRotation, device_id)
    credential = db.get(DeviceCredential, device_id)
    node = db.get(Repeater, device_id)
    assert (
        rotation.cert_serial == credential.cert_serial == node.cert_serial == public["cert_serial"]
    )
    assert rotation.request_id == public["request_id"]
    assert rotation.previous_cert_serial == "synthetic"
    assert rotation.csr_public_key_sha256 == credential.csr_public_key_sha256
    assert rotation.node_reported_at is None and rotation.node_reported_boot_id is None
    assert db.scalar(select(func.count()).select_from(DeviceCertificateRotation)) == 1
    assert db.scalar(select(func.count()).select_from(Certificate)) == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "device_certificate_issued",
                AuditLog.target_id == device_id,
            )
        )
        == 1
    )
    cert = db.scalar(select(Certificate).where(Certificate.serial == public["cert_serial"]))
    assert cert is not None and cert.cn == "device:" + device_id
    return cert


@pytest.mark.parametrize("same_request", [True, False], ids=["exact-retry", "pending-conflict"])
def test_concurrent_renewals_issue_once(renewal, monkeypatch, same_request):
    factory, engine, device_id, request, bearer, payload, issued = renewal
    second_payload = (
        payload if same_request else payload.model_copy(update={"request_id": str(uuid4())})
    )
    entered, release = pause_issuance(monkeypatch)

    def second(db):
        if same_request:
            return renew(db, request, bearer, second_payload)
        with pytest.raises(HTTPException) as conflict:
            renew(db, request, bearer, second_payload)
        assert conflict.value.status_code == 409
        assert conflict.value.detail == "Certificate renewal is pending"

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first, first_state = submit(
                pool, factory, lambda db: renew(db, request, bearer, payload)
            )
            assert entered.wait(5), "First renewal did not enter PKI with locks held"
            competing, competing_state = submit(pool, factory, second, stale_device_id=device_id)
            wait_for_blocker(engine, competing_state["pid"], first_state["pid"])
            release.set()
            public = first.result(timeout=20)
            other = competing.result(timeout=20)
            if same_request:
                assert other == public  # Entire public response, including PEM/time/fingerprint.
            else:
                assert other is None
        finally:
            release.set()  # Release before executor shutdown, even on failed assertions.
    assert issued == [public["cert_serial"]]
    with factory() as db:
        assert assert_issuance(db, device_id, public).revoked_at is None
        assert (
            db.scalar(select(Certificate).where(Certificate.serial == "synthetic")).revoked_at
            is None
        )
        assert db.get(DeviceCredential, device_id).revoked_at is None


def test_revocation_commits_before_waiting_renewal_denies_stale_auth(renewal):
    factory, engine, device_id, request, bearer, payload, issued = renewal

    def rejected(db):
        with pytest.raises(HTTPException) as denied:
            renew(db, request, bearer, payload)
        assert denied.value.status_code == 401

    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as admin:
            try:
                admin_pid = admin.scalar(text("SELECT pg_backend_pid()"))
                revoke_device_credentials(admin, device_id)
                future, state = submit(pool, factory, rejected, stale_device_id=device_id)
                wait_for_blocker(engine, state["pid"], admin_pid)
                admin.commit()
            finally:
                admin.rollback()  # On failure, release the lock before executor shutdown.
        future.result(timeout=20)
    assert issued == []
    with factory() as db:
        assert db.get(DeviceCertificateRotation, device_id) is None
        assert db.get(DeviceCredential, device_id).revoked_at is not None
        assert db.get(DeviceCredential, device_id).cert_serial == "synthetic"
        assert db.get(Repeater, device_id).cert_serial == "synthetic"
        assert (
            db.scalar(select(Certificate).where(Certificate.serial == "synthetic")).revoked_at
            is not None
        )
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert (
            db.scalar(
                select(DeviceEnrollment).where(
                    DeviceEnrollment.repeater_id == device_id,
                )
            ).consumed_at
            is not None
        )


def test_waiting_revocation_invalidates_committed_renewal(renewal, monkeypatch):
    factory, engine, device_id, request, bearer, payload, issued = renewal
    entered, release = pause_issuance(monkeypatch)

    def administration(db):
        revoke_device_credentials(db, device_id)
        db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first, state = submit(pool, factory, lambda db: renew(db, request, bearer, payload))
            assert entered.wait(5), "Renewal did not enter PKI with locks held"
            admin, admin_state = submit(pool, factory, administration)
            wait_for_blocker(engine, admin_state["pid"], state["pid"])
            release.set()
            public = first.result(timeout=20)
            admin.result(timeout=20)
        finally:
            release.set()
    assert issued == [public["cert_serial"]]
    with factory() as db:
        assert assert_issuance(db, device_id, public).revoked_at is not None
        assert (
            db.scalar(select(Certificate).where(Certificate.serial == "synthetic")).revoked_at
            is not None
        )
        assert db.get(DeviceCredential, device_id).revoked_at is not None
        assert (
            db.scalar(
                select(DeviceEnrollment).where(
                    DeviceEnrollment.repeater_id == device_id,
                )
            ).consumed_at
            is not None
        )
        with pytest.raises(HTTPException) as denied:
            get_current_device(request=request, credentials=bearer, db=db)
        assert denied.value.status_code == 401
        db.rollback()
        with pytest.raises(HTTPException) as retry_denied:
            renew(db, request, bearer, payload)
        assert retry_denied.value.status_code == 401
        db.rollback()
    assert issued == [public["cert_serial"]]
