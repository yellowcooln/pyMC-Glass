"""Server-only CSR renewal contract; synthetic authority and real temporary PKI."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.models import (
    AuditLog,
    Certificate,
    DeviceCertificateRotation,
    DeviceCredential,
    Repeater,
)
from app.db.session import get_session_factory
from app.security.tokens import hash_token
from app.services.pki import PkiService
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

URL = "/device/certificates/renew"
REPORT_URL = "/device/certificates/report"


@pytest.fixture
def report(renewal):
    issued = post(renewal)
    assert issued.status_code == 200
    public = issued.json()
    return {
        "device_id": public["device_id"],
        "request_id": public["request_id"],
        "cert_serial": public["cert_serial"],
        "fingerprint_sha256": public["fingerprint_sha256"],
        "boot_id": str(uuid4()),
        "connected": True,
    }


def test_report_current_idempotent_and_new_boot(renewal, report):
    client, auth, _, _ = renewal
    first_at = None
    for index, boot_id in enumerate([report["boot_id"], report["boot_id"], str(uuid4())]):
        response = client.post(REPORT_URL, headers=auth, json=dict(report, boot_id=boot_id))
        assert response.status_code == 200
        assert response.json() == {
            "device_id": report["device_id"],
            "request_id": report["request_id"],
            "cert_serial": report["cert_serial"],
            "accepted": True,
            "state": "node_reported",
        }
        assert response.headers["cache-control"] == "no-store"
        with get_session_factory()() as db:
            rotation = db.get(DeviceCertificateRotation, report["device_id"])
            assert rotation.node_reported_boot_id == boot_id
            assert rotation.node_reported_at is not None
            if index == 0:
                first_at = rotation.node_reported_at
            elif index == 1:
                assert rotation.node_reported_at == first_at
            else:
                assert rotation.node_reported_at > first_at
            audits = db.scalars(
                select(AuditLog).where(AuditLog.action == "device_certificate_node_reported")
            ).all()
            assert len(audits) == (1 if index < 2 else 2)
            assert audits[-1].target_id == report["device_id"]
            assert audits[-1].user_id is None
            assert auth["Authorization"].split()[1] not in audits[-1].details_json
            assert (
                db.scalar(select(Certificate).where(Certificate.serial == "old")).revoked_at is None
            )


def make_csr(key_size=2048):
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "untrusted-name")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode(), key


@pytest.fixture
def renewal(client):
    client.base_url = "https://testserver"
    device_id, token = str(uuid4()), secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with get_session_factory()() as db:
        db.add(
            Repeater(
                id=device_id,
                node_name="node",
                pubkey="ab" * 32,
                status="adopted",
                cert_serial="old",
            )
        )
        db.flush()
        db.add(
            DeviceCredential(
                repeater_id=device_id,
                token_hash=hash_token(token),
                cert_serial="old",
                csr_public_key_sha256="a" * 64,
            )
        )
        db.add(
            Certificate(
                repeater_id=device_id,
                serial="old",
                cn="device:" + device_id,
                issued_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
                pem_hash="b" * 64,
            )
        )
        db.commit()
    csr, key = make_csr()
    return (
        client,
        {"Authorization": "Bearer " + token},
        {
            "device_id": device_id,
            "request_id": str(uuid4()),
            "csr_pem": csr,
        },
        key,
    )


def post(renewal, **changes):
    client, auth, body, _ = renewal
    return client.post(URL, headers=auth, json=dict(body, **changes))


def test_public_response_exact_retry_and_atomic_state(renewal, monkeypatch):
    client, auth, body, key = renewal
    first = post(renewal)
    assert first.status_code == 200
    public = first.json()
    assert set(public) == {
        "device_id",
        "request_id",
        "client_cert",
        "ca_cert",
        "cert_serial",
        "expires_at",
        "fingerprint_sha256",
        "state",
    }
    assert public["state"] == "issued"
    assert first.headers["cache-control"] == "no-store"
    assert "PRIVATE KEY" not in first.text
    cert = x509.load_pem_x509_certificate(public["client_cert"].encode())
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == (
        "device:" + body["device_id"]
    )
    assert public["fingerprint_sha256"] == cert.fingerprint(hashes.SHA256()).hex()
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, body["device_id"])
        credential = db.get(DeviceCredential, body["device_id"])
        node = db.get(Repeater, body["device_id"])
        assert rotation.previous_cert_serial == "old"
        assert rotation.node_reported_at is None and rotation.node_reported_boot_id is None
        assert rotation.credential_token_hash == hash_token(auth["Authorization"].split()[1])
        assert rotation.cert_serial == credential.cert_serial == node.cert_serial
        assert credential.csr_public_key_sha256 == rotation.csr_public_key_sha256
        record = db.scalar(select(Certificate).where(Certificate.serial == public["cert_serial"]))
        assert record.cn == "device:" + body["device_id"]
        assert record.pem_hash == hashlib.sha256(public["client_cert"].encode()).hexdigest()
        assert db.scalar(select(Certificate).where(Certificate.serial == "old")).revoked_at is None
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "device_certificate_issued"))
        assert audit.target_id == body["device_id"] and audit.user_id is None
        assert body["csr_pem"] not in audit.details_json
        assert auth["Authorization"].split()[1] not in audit.details_json
        node.node_name = "renamed"
        db.commit()

    def never_issue(*args, **kwargs):
        pytest.fail("retry must not issue a new certificate")

    monkeypatch.setattr(PkiService, "issue_device_certificate", never_issue)
    retry = client.post(URL, headers=auth, json=body)
    assert retry.status_code == 200 and retry.json() == public
    assert retry.headers["cache-control"] == "no-store"
    with get_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Certificate)) == 2
        assert db.scalar(select(func.count()).select_from(DeviceCertificateRotation)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
        {
            "X-Device-Id": "forged",
            "X-Client-Cert": "trusted",
        },
    ],
)
def test_auth_required(renewal, headers):
    client, _, body, _ = renewal
    assert client.post(URL, headers=headers, json=body).status_code == 401


def test_https_and_identity(renewal):
    client, auth, body, _ = renewal
    assert (
        client.post(
            "http://testserver" + URL,
            headers=dict(auth, **{"X-Forwarded-Proto": "https"}),
            json=body,
        ).status_code
        == 400
    )
    assert post(renewal, device_id=str(uuid4())).status_code == 401
    assert post(renewal).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"csr_pem": "not a csr"},
        {"csr_pem": "x" * 14001},
        {"request_id": "not-uuid"},
        {"request_id": str(uuid4()).upper()},
        {"device_id": "bad"},
        {"extra": "secret-input"},
    ],
)
def test_invalid_request_sanitized(renewal, changes):
    response = post(renewal, **changes)
    assert response.status_code == 422
    assert "secret-input" not in response.text
    assert renewal[2]["csr_pem"] not in response.text
    with get_session_factory()() as db:
        assert db.get(DeviceCertificateRotation, renewal[2]["device_id"]) is None


def test_weak_and_invalid_signature(renewal):
    weak, _ = make_csr(1024)
    assert post(renewal, csr_pem=weak).status_code == 422
    import base64
    import textwrap

    csr = x509.load_pem_x509_csr(renewal[2]["csr_pem"].encode())
    der = bytearray(csr.public_bytes(serialization.Encoding.DER))
    der[-1] ^= 1
    bad = (
        "-----BEGIN CERTIFICATE REQUEST-----\n"
        + "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64))
        + "\n-----END CERTIFICATE REQUEST-----\n"
    )
    assert post(renewal, csr_pem=bad).status_code == 422


def test_stream_bounds_without_content_length(renewal):
    client, auth, _, _ = renewal
    response = client.post(URL, headers=auth, content=iter([b" " * 8192, b" " * 8193]))
    assert response.status_code == 413
    assert client.post(URL, headers=auth, content=b"{").status_code == 422
    # Exactly the limit is parsed, not rejected as oversized.
    assert client.post(URL, headers=auth, content=b" " * 16384).status_code == 422


def test_key_replay_and_unreported_pending(renewal):
    assert post(renewal).status_code == 200
    different, _ = make_csr()
    assert post(renewal, csr_pem=different).status_code == 409
    assert post(renewal, request_id=str(uuid4())).status_code == 409
    assert post(renewal).status_code == 200


@pytest.mark.parametrize("change", ["credential_serial", "node_serial", "revoked_certificate"])
def test_stale_or_revoked_replay(renewal, change):
    issued = post(renewal).json()
    with get_session_factory()() as db:
        device_id = renewal[2]["device_id"]
        if change == "credential_serial":
            db.get(DeviceCredential, device_id).cert_serial = "changed"
        elif change == "node_serial":
            db.get(Repeater, device_id).cert_serial = "changed"
        else:
            db.scalar(
                select(Certificate).where(Certificate.serial == issued["cert_serial"])
            ).revoked_at = datetime.now(UTC)
        db.commit()
    assert post(renewal).status_code == 409


@pytest.mark.parametrize("change", ["revoked_credential", "rejected", "pending_adoption"])
def test_revoked_device_and_parent_status(renewal, change):
    with get_session_factory()() as db:
        device_id = renewal[2]["device_id"]
        if change == "revoked_credential":
            db.get(DeviceCredential, device_id).revoked_at = datetime.now(UTC)
        else:
            db.get(Repeater, device_id).status = change
        db.commit()
    assert post(renewal).status_code == 401


@pytest.mark.parametrize("completed", [False, True])
def test_expired_or_reported_pending_allows_new_request(renewal, completed):
    first = post(renewal).json()
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, renewal[2]["device_id"])
        if completed:
            rotation.issued_at = datetime.now(UTC) - timedelta(seconds=3601)
            rotation.node_reported_at = datetime.now(UTC)
            rotation.node_reported_boot_id = str(uuid4())
        else:
            rotation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    response = post(renewal, request_id=str(uuid4()))
    assert response.status_code == 200
    assert response.json()["cert_serial"] != first["cert_serial"]
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, renewal[2]["device_id"])
        assert rotation.previous_cert_serial == first["cert_serial"]
        assert rotation.node_reported_at is None and rotation.node_reported_boot_id is None
        assert db.scalar(select(func.count()).select_from(DeviceCertificateRotation)) == 1


def test_new_enrollment_cannot_replay_old_response(renewal):
    first = post(renewal).json()
    client, old_auth, body, _ = renewal
    new_token = secrets.token_urlsafe(32)
    with get_session_factory()() as db:
        credential = db.get(DeviceCredential, body["device_id"])
        credential.token_hash = hash_token(new_token)
        credential.cert_serial = "reenrolled"
        db.get(Repeater, body["device_id"]).cert_serial = "reenrolled"
        db.commit()
    assert client.post(URL, headers=old_auth, json=body).status_code == 401
    response = client.post(URL, headers={"Authorization": "Bearer " + new_token}, json=body)
    assert response.status_code == 200
    assert response.json()["cert_serial"] != first["cert_serial"]


@pytest.mark.parametrize("failure,expected", [("pki", 503), ("commit", 503), ("integrity", 409)])
def test_failure_rolls_back_every_write(renewal, monkeypatch, failure, expected):
    def fail(*args, **kwargs):
        if failure == "integrity":
            raise IntegrityError("synthetic", {}, RuntimeError("secret failure"))
        raise RuntimeError("secret failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            PkiService if failure == "pki" else Session,
            "issue_device_certificate" if failure == "pki" else "commit",
            fail,
        )
        response = post(renewal)
    assert response.status_code == expected and "secret failure" not in response.text
    with get_session_factory()() as db:
        device_id = renewal[2]["device_id"]
        assert db.get(DeviceCertificateRotation, device_id) is None
        assert db.get(DeviceCredential, device_id).cert_serial == "old"
        assert db.get(Repeater, device_id).cert_serial == "old"
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert post(renewal).status_code == 200


@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize("reporting", [False, True])
def test_stream_finishes_before_session_and_locked_auth(renewal, monkeypatch, revoke, reporting):
    import asyncio
    import json

    import httpx
    from app.security import devices

    client, auth, body, _ = renewal
    events = []
    url = URL
    if reporting:
        public = post(renewal).json()
        body = {
            key: public[key]
            for key in ("device_id", "request_id", "cert_serial", "fingerprint_sha256")
        }
        body.update(boot_id=str(uuid4()), connected=True)
        url = REPORT_URL
    original_init = Session.__init__
    original_lock = devices.lock_repeater
    external_session = False

    def opened(self, *args, **kwargs):
        if not external_session:
            events.append("session-opened")
        original_init(self, *args, **kwargs)

    def locked(*args, **kwargs):
        result = original_lock(*args, **kwargs)
        events.append("parent-lock-acquired")
        return result

    monkeypatch.setattr(Session, "__init__", opened)
    monkeypatch.setattr(devices, "lock_repeater", locked)

    async def chunks():
        nonlocal external_session
        events.append("body-stream-start")
        yield b" "
        await asyncio.sleep(0)
        events.append("body-stream-wait")
        if revoke:
            external_session = True
            try:
                with get_session_factory()() as db:
                    devices.revoke_device_credentials(db, body["device_id"])
                    db.commit()
                events.append("revocation-committed")
            finally:
                external_session = False
        yield json.dumps(body).encode()
        events.append("body-stream-complete")

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="https://testserver"
        ) as http:
            return await http.post(url, headers=auth, content=chunks())

    response = asyncio.run(send())
    expected = ["body-stream-start", "body-stream-wait"]
    if revoke:
        expected.append("revocation-committed")
    expected.extend(["body-stream-complete", "session-opened", "parent-lock-acquired"])
    assert events == expected
    assert response.status_code == (401 if revoke else 200)
    if revoke:
        with get_session_factory()() as db:
            if reporting:
                assert db.get(DeviceCertificateRotation, body["device_id"]).node_reported_at is None
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.action == "device_certificate_node_reported")
                    )
                    == 0
                )
                return
            assert db.get(DeviceCertificateRotation, body["device_id"]) is None
            assert db.get(DeviceCredential, body["device_id"]).cert_serial == "old"
            assert db.get(Repeater, body["device_id"]).cert_serial == "old"
            assert db.scalar(select(func.count()).select_from(Certificate)) == 1
            assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize("failure", ["deadline", "stalled", "cancel"])
@pytest.mark.parametrize("reporting", [False, True])
def test_stream_deadline_and_cancellation_open_no_session(renewal, monkeypatch, failure, reporting):
    import asyncio

    import httpx
    from app.api.routes import device_certificates

    client, auth, _, _ = renewal
    monkeypatch.setattr(device_certificates, "RENEWAL_BODY_TIMEOUT_SECONDS", 0.02, raising=False)
    monkeypatch.setattr(device_certificates, "REPORT_BODY_TIMEOUT_SECONDS", 0.02)
    url = REPORT_URL if reporting else URL
    label = "report" if reporting else "renewal"

    def forbidden(*args, **kwargs):
        pytest.fail("body ingestion must not open a database session")

    monkeypatch.setattr(Session, "__init__", forbidden)

    async def chunks():
        yield b"{secret-input"
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "stalled":
            await asyncio.Event().wait()
        # Keep sending chunks: the deadline must bound the total, not each read.
        while True:
            await asyncio.sleep(0.005)
            yield b" "

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="https://testserver"
        ) as http:
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await http.post(url, headers=auth, content=chunks())
            else:
                response = await http.post(url, headers=auth, content=chunks())
                assert response.status_code == 408
                assert response.json() == {"detail": f"Certificate {label} body timed out"}
                assert "secret-input" not in response.text

    asyncio.run(send())


@pytest.mark.parametrize("content,status", [(b"{", 422), (b" " * 16385, 413)])
def test_rejected_body_opens_no_session(renewal, monkeypatch, content, status):
    client, auth, _, _ = renewal

    def forbidden(*args, **kwargs):
        pytest.fail("rejected body must not open a database session")

    monkeypatch.setattr(Session, "__init__", forbidden)
    assert client.post(URL, headers=auth, content=content).status_code == status


def test_route_runs_crypto_off_event_loop(renewal, monkeypatch):
    import asyncio

    original = PkiService.issue_device_certificate

    def checked(self, **kwargs):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return original(self, **kwargs)

    monkeypatch.setattr(PkiService, "issue_device_certificate", checked)
    assert post(renewal).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"connected": False},
        {"connected": 1},
        {"connected": "true"},
        {"cert_serial": "0"},
        {"cert_serial": "00"},
        {"cert_serial": "AB"},
        {"cert_serial": "a" * 41},
        {"fingerprint_sha256": "A" * 64},
        {"fingerprint_sha256": "a" * 63},
        {"boot_id": "invalid"},
        {"boot_id": str(uuid4()).upper()},
        {"request_id": "invalid"},
        {"device_id": "invalid"},
        {"private_key": "secret-input"},
    ],
)
def test_report_malformed_before_session(renewal, report, monkeypatch, changes):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid report must not open a session")

    monkeypatch.setattr(Session, "__init__", forbidden)
    response = renewal[0].post(REPORT_URL, headers=renewal[1], json=dict(report, **changes))
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid certificate report request"}
    assert "secret-input" not in response.text


@pytest.mark.parametrize(
    "content,status",
    [
        (b"{secret-input", 422),
        (b" " * 2048, 422),
        (b" " * 2049, 413),
    ],
)
def test_report_body_bound_before_session(renewal, monkeypatch, content, status):
    def forbidden(*args, **kwargs):
        pytest.fail("rejected report must not open a session")

    monkeypatch.setattr(Session, "__init__", forbidden)
    response = renewal[0].post(REPORT_URL, headers=renewal[1], content=iter([content]))
    assert response.status_code == status
    assert "secret-input" not in response.text


@pytest.mark.parametrize(
    "change,expected",
    [
        ("request", 409),
        ("serial", 409),
        ("fingerprint", 409),
        ("device", 401),
        ("credential_serial", 409),
        ("parent_serial", 409),
        ("generation", 409),
        ("revoked_cert", 409),
        ("expired_cert", 409),
        ("expired_rotation", 409),
        ("missing_cert", 409),
        ("missing_rotation", 409),
        ("revoked_credential", 401),
        ("rejected", 401),
        ("pending_adoption", 401),
    ],
)
def test_report_rejects_stale_authority_without_writes(renewal, report, change, expected):
    body = dict(report)
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        credential = db.get(DeviceCredential, report["device_id"])
        parent = db.get(Repeater, report["device_id"])
        cert = db.scalar(select(Certificate).where(Certificate.serial == report["cert_serial"]))
        if change in ("request", "device"):
            body[change + "_id"] = str(uuid4())
        elif change == "serial":
            body["cert_serial"] = "1"
        elif change == "fingerprint":
            body["fingerprint_sha256"] = "0" * 64
        elif change == "credential_serial":
            credential.cert_serial = "other"
        elif change == "parent_serial":
            parent.cert_serial = "other"
        elif change == "generation":
            rotation.credential_token_hash = "0" * 64
        elif change == "revoked_cert":
            cert.revoked_at = datetime.now(UTC)
        elif change == "expired_cert":
            cert.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "expired_rotation":
            rotation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "missing_cert":
            db.delete(cert)
        elif change == "missing_rotation":
            db.delete(rotation)
        elif change == "revoked_credential":
            credential.revoked_at = datetime.now(UTC)
        else:
            parent.status = change
        db.commit()
    response = renewal[0].post(REPORT_URL, headers=renewal[1], json=body)
    assert response.status_code == expected
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        assert rotation is None or rotation.node_reported_at is None
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
        {"X-Device-Id": "forged", "X-Client-Cert": "trusted"},
    ],
)
def test_report_requires_machine_bearer(renewal, report, headers):
    assert renewal[0].post(REPORT_URL, headers=headers, json=report).status_code == 401


def test_report_requires_https(renewal, report):
    assert (
        renewal[0]
        .post(
            "http://testserver" + REPORT_URL,
            headers=dict(renewal[1], **{"X-Forwarded-Proto": "https"}),
            json=report,
        )
        .status_code
        == 400
    )


@pytest.mark.parametrize("failure,expected", [("audit", 503), ("commit", 503), ("integrity", 409)])
def test_report_transaction_rollback(renewal, report, monkeypatch, failure, expected):
    from app.api.routes import device_certificates

    def fail(*args, **kwargs):
        if failure == "integrity":
            raise IntegrityError("synthetic", {}, RuntimeError("secret failure"))
        raise RuntimeError("secret failure")

    with monkeypatch.context() as patch:
        if failure == "audit":
            patch.setattr(device_certificates, "write_audit_log", fail)
        else:
            patch.setattr(Session, "commit", fail)
        response = renewal[0].post(REPORT_URL, headers=renewal[1], json=report)
    assert response.status_code == expected
    assert "secret failure" not in response.text
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        assert rotation.node_reported_at is None and rotation.node_reported_boot_id is None
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert db.scalar(select(func.count()).select_from(Certificate)) == 2
    assert renewal[0].post(REPORT_URL, headers=renewal[1], json=report).status_code == 200


def controlled_issuance_clock(monkeypatch):
    from app.api.routes import device_certificates
    from app.services import pki

    class Clock(datetime):
        current = datetime.now(UTC).replace(microsecond=0)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    # Replace only module references, never datetime's global class/methods.
    monkeypatch.setattr(device_certificates, "datetime", Clock)
    monkeypatch.setattr(pki, "datetime", Clock)
    return Clock


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("reported", [False, True])
def test_real_issuance_exact_hour_boundary(renewal, monkeypatch, reported, legacy):
    clock = controlled_issuance_clock(monkeypatch)
    actual = clock.current
    first = post(renewal)
    assert first.status_code == 200
    public = first.json()
    leaf = x509.load_pem_x509_certificate(public["client_cert"].encode())
    assert leaf.not_valid_before_utc == actual - timedelta(minutes=5)
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, public["device_id"])
        certificate = db.scalar(
            select(Certificate).where(Certificate.serial == public["cert_serial"])
        )
        assert certificate.issued_at.replace(tzinfo=UTC) == leaf.not_valid_before_utc
        if legacy:
            # Reproduce the deployed alias without changing historical Certificate rows.
            rotation.issued_at = leaf.not_valid_before_utc
            db.commit()
    if reported:
        assertion = {
            key: public[key]
            for key in ("device_id", "request_id", "cert_serial", "fingerprint_sha256")
        }
        assertion.update(boot_id=str(uuid4()), connected=True)
        assert renewal[0].post(REPORT_URL, headers=renewal[1], json=assertion).status_code == 200
    for seconds in (3599, 3600):
        clock.current = actual + timedelta(seconds=seconds)
        assert post(renewal).json() == public
        response = post(renewal, request_id=str(uuid4()))
        if seconds == 3599 or not reported:
            assert response.status_code == 409
            assert response.json() == {
                "detail": "Certificate renewal rate limited"
                if seconds == 3599
                else "Certificate renewal is pending"
            }
            with get_session_factory()() as db:
                rotation = db.get(DeviceCertificateRotation, public["device_id"])
                expected = leaf.not_valid_before_utc if legacy else actual
                assert rotation.issued_at.replace(tzinfo=UTC) == expected
        else:
            assert response.status_code == 200
            assert response.json()["cert_serial"] != public["cert_serial"]
    with get_session_factory()() as db:
        certificate = db.scalar(
            select(Certificate).where(Certificate.serial == public["cert_serial"])
        )
        assert certificate.issued_at.replace(tzinfo=UTC) == leaf.not_valid_before_utc
        assert db.scalar(select(func.count()).select_from(Certificate)) == (3 if reported else 2)


def test_rotation_timestamp_is_after_real_pki_returns(renewal, monkeypatch):
    clock = controlled_issuance_clock(monkeypatch)
    started = clock.current
    original = PkiService.issue_device_certificate

    def delayed(service, **kwargs):
        bundle = original(service, **kwargs)
        clock.current += timedelta(seconds=2, microseconds=123456)
        return bundle

    monkeypatch.setattr(PkiService, "issue_device_certificate", delayed)
    public = post(renewal).json()
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, public["device_id"])
        certificate = db.scalar(
            select(Certificate).where(Certificate.serial == public["cert_serial"])
        )
        assert rotation.issued_at.replace(tzinfo=UTC) == clock.current
        assert certificate.issued_at.replace(tzinfo=UTC) == started - timedelta(minutes=5)


def test_invalid_rate_metadata_is_sanitized_but_retry_and_expiry_escape_survive(renewal):
    public = post(renewal).json()
    with get_session_factory()() as db:
        db.get(
            DeviceCertificateRotation, public["device_id"]
        ).client_cert_pem = "secret-corrupt-pem"
        db.commit()
    response = post(renewal, request_id=str(uuid4()))
    assert response.status_code == 503
    assert response.json() == {"detail": "Certificate issuance unavailable"}
    assert "secret-corrupt-pem" not in response.text
    assert post(renewal).status_code == 200
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, public["device_id"])
        assert rotation.request_id == public["request_id"]
        rotation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert post(renewal, request_id=str(uuid4())).status_code == 200


def test_old_report_never_resurrects_after_next_issuance(renewal, report):
    assert renewal[0].post(REPORT_URL, headers=renewal[1], json=report).status_code == 200
    with get_session_factory()() as db:
        db.get(DeviceCertificateRotation, report["device_id"]).issued_at = datetime.now(
            UTC
        ) - timedelta(seconds=3601)
        db.commit()
    assert post(renewal, request_id=str(uuid4())).status_code == 200
    assert renewal[0].post(REPORT_URL, headers=renewal[1], json=report).status_code == 409
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        assert rotation.node_reported_at is None and rotation.node_reported_boot_id is None


def test_expired_exact_retry_metadata_and_rate_escape(renewal, report):
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        rotation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        cert = db.scalar(select(Certificate).where(Certificate.serial == report["cert_serial"]))
        cert.expires_at = rotation.expires_at
        db.commit()
    assert post(renewal).status_code == 200
    assert post(renewal, request_id=str(uuid4())).status_code == 200


def test_report_valid_body_exact_byte_limit(renewal, report):
    import json

    data = json.dumps(report).encode()
    data += b" " * (2048 - len(data))
    assert len(data) == 2048
    response = renewal[0].post(REPORT_URL, headers=renewal[1], content=iter([data[:32], data[32:]]))
    assert response.status_code == 200


def test_report_old_generation_after_reenrollment(renewal, report):
    new_token = secrets.token_urlsafe(32)
    with get_session_factory()() as db:
        db.get(DeviceCredential, report["device_id"]).token_hash = hash_token(new_token)
        db.commit()
    client, old_auth, _, _ = renewal
    assert client.post(REPORT_URL, headers=old_auth, json=report).status_code == 401
    new_auth = {"Authorization": "Bearer " + new_token}
    assert client.post(REPORT_URL, headers=new_auth, json=report).status_code == 409
    with get_session_factory()() as db:
        assert db.get(DeviceCertificateRotation, report["device_id"]).node_reported_at is None
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_new_boot_commit_failure_preserves_prior_assertion(renewal, report, monkeypatch):
    client, auth, _, _ = renewal
    assert client.post(REPORT_URL, headers=auth, json=report).status_code == 200
    with get_session_factory()() as db:
        before = db.get(DeviceCertificateRotation, report["device_id"]).node_reported_at

    def fail(*args, **kwargs):
        raise RuntimeError("secret failure")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail)
        response = client.post(REPORT_URL, headers=auth, json=dict(report, boot_id=str(uuid4())))
    assert response.status_code == 503 and "secret failure" not in response.text
    with get_session_factory()() as db:
        rotation = db.get(DeviceCertificateRotation, report["device_id"])
        assert rotation.node_reported_at == before
        assert rotation.node_reported_boot_id == report["boot_id"]
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 2
