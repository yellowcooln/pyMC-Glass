"""Real HTTP enrollment and device-auth regression tests; all secrets synthetic."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.models import Repeater
from app.db.session import get_session_factory
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import Depends


@pytest.fixture
def setup(client):
    client.base_url = "https://testserver"
    assert (
        client.post(
            "/api/bootstrap/admin",
            json={"email": "admin@example.com", "password": "verysecurepassword123"},
        ).status_code
        == 200
    )
    login = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "verysecurepassword123"}
    )
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    device_id = str(uuid4())
    with get_session_factory()() as db:
        db.add(Repeater(id=device_id, node_name="node-one", pubkey="ab" * 32, status="adopted"))
        db.commit()
    from app.security.devices import get_current_device

    # A test-only route exercises the real dependency, without overriding it.
    @client.app.get("/test/device")
    def identity(device=Depends(get_current_device)):
        return {"device_id": device.id, "node_name": device.node_name}

    return client, headers, device_id


def payload(setup, *, key_size=2048):
    client, headers, device_id = setup
    response = client.post(f"/api/adoption/{device_id}/enrollment", headers=headers)
    assert response.status_code == 200
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "untrusted")]))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return {
        "device_id": device_id,
        "node_name": "node-one",
        "pubkey": "ab" * 32,
        "enrollment_token": response.json()["enrollment_token"],
        "operational_token": secrets.token_urlsafe(32),
        "csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode(),
    }, key


def test_enrollment_certificate_replay_rename_and_revoke(setup):
    from app.db.models import DeviceCredential, DeviceEnrollment
    from app.security.tokens import hash_token

    client, admin, device_id = setup
    body, key = payload(setup)
    enrolled = client.post("/enroll", json=body)
    assert enrolled.status_code == 200
    assert set(enrolled.json()) == {
        "device_id",
        "client_cert",
        "ca_cert",
        "cert_serial",
        "expires_at",
    }
    cert = x509.load_pem_x509_certificate(enrolled.json()["client_cert"].encode())
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert (
        cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "device:" + device_id
    )
    assert cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier) == [
        "urn:openhop:device:" + device_id
    ]
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert list(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == [
        ExtendedKeyUsageOID.CLIENT_AUTH
    ]
    assert client.post("/enroll", json=body).status_code == 401
    auth = {"Authorization": "Bearer " + body["operational_token"]}
    assert client.get("/test/device", headers=auth).json()["device_id"] == device_id
    assert (
        client.get(
            "/test/device", headers={"X-Client-Cert": "trusted", "X-Device-Id": device_id}
        ).status_code
        == 401
    )
    with get_session_factory()() as db:
        credential = db.get(DeviceCredential, device_id)
        assert credential.token_hash == hash_token(body["operational_token"])
        import hashlib

        expected_fingerprint = hashlib.sha256(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).hexdigest()
        assert credential.csr_public_key_sha256 == expected_fingerprint
        assert db.get(DeviceEnrollment, device_id).consumed_at is not None
        db.get(Repeater, device_id).node_name = "renamed"
        db.commit()
    assert client.get("/test/device", headers=auth).json()["node_name"] == "renamed"
    assert (
        client.post(f"/api/adoption/{device_id}/credentials/revoke", headers=admin).status_code
        == 200
    )
    assert client.get("/test/device", headers=auth).status_code == 401


@pytest.mark.parametrize(
    "field,value", [("node_name", "other"), ("pubkey", "cd" * 32), ("device_id", str(uuid4()))]
)
def test_identity_mismatch_does_not_consume(setup, field, value):
    client, _, _ = setup
    body, _ = payload(setup)
    bad = dict(body, **{field: value})
    assert client.post("/enroll", json=bad).status_code == 401
    assert client.post("/enroll", json=body).status_code == 200


def test_expiry_replacement_and_https(setup):
    from app.db.models import DeviceEnrollment

    client, admin, device_id = setup
    body, _ = payload(setup)
    assert (
        client.post(
            "http://testserver/enroll", json=body, headers={"X-Forwarded-Proto": "https"}
        ).status_code
        == 400
    )
    with get_session_factory()() as db:
        db.get(DeviceEnrollment, device_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.post("/enroll", json=body).status_code == 401
    newer, _ = payload(setup)
    assert client.post("/enroll", json=body).status_code == 401
    assert client.post("/enroll", json=newer).status_code == 200
    auth = {"Authorization": "Bearer " + newer["operational_token"]}
    assert (
        client.get(
            "http://testserver/test/device", headers=dict(auth, **{"X-Forwarded-Proto": "https"})
        ).status_code
        == 400
    )
    assert (
        client.post(f"/api/adoption/{device_id}/reject", json={}, headers=admin).status_code == 200
    )
    assert client.get("/test/device", headers=auth).status_code == 401
    assert client.post(f"/api/adoption/{device_id}/enrollment", headers=admin).status_code == 409


def test_invalid_csr_and_issuance_rollback(setup, monkeypatch):
    from app.db.models import DeviceCredential, DeviceEnrollment
    from app.services.pki import PkiService

    client, _, device_id = setup
    body, _ = payload(setup)
    assert client.post("/enroll", json=dict(body, csr_pem="not a csr")).status_code == 422
    weak, _ = payload(setup, key_size=1024)
    assert client.post("/enroll", json=weak).status_code == 422
    original = PkiService.issue_device_certificate

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(PkiService, "issue_device_certificate", fail)
    assert client.post("/enroll", json=body).status_code == 401  # superseded token
    assert client.post("/enroll", json=weak).status_code == 422
    valid, _ = payload(setup)
    assert client.post("/enroll", json=valid).status_code == 503
    with get_session_factory()() as db:
        assert db.get(DeviceEnrollment, device_id).consumed_at is None
        assert db.get(DeviceCredential, device_id) is None
    monkeypatch.setattr(PkiService, "issue_device_certificate", original)
    assert client.post("/enroll", json=valid).status_code == 200


def test_body_bounds_and_secret_errors(setup):
    client, _, _ = setup
    body, _ = payload(setup)
    assert client.post("/enroll", content=b"x" * 16385).status_code == 413
    bad = dict(body, operational_token="short")
    response = client.post("/enroll", json=bad)
    assert response.status_code == 422
    assert body["enrollment_token"] not in response.text
    assert body["csr_pem"] not in response.text


def test_signature_failure_and_binding(setup):
    from app.security.devices import bind_device_identity

    client, _, device_id = setup
    body, _ = payload(setup)
    csr = x509.load_pem_x509_csr(body["csr_pem"].encode())
    der = bytearray(csr.public_bytes(serialization.Encoding.DER))
    der[-1] ^= 1
    import base64
    import textwrap

    corrupted = (
        "-----BEGIN CERTIFICATE REQUEST-----\n"
        + "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64))
        + "\n-----END CERTIFICATE REQUEST-----\n"
    )
    assert client.post("/enroll", json=dict(body, csr_pem=corrupted)).status_code == 422
    assert client.post("/enroll", json=body).status_code == 200
    from app.security.devices import get_current_device

    @client.app.get("/test/bind/{claimed_id}/{pubkey}")
    def bind(claimed_id: str, pubkey: str, device=Depends(get_current_device)):
        bind_device_identity(device, device_id=claimed_id, pubkey=pubkey)
        return {"ok": True}

    auth = {"Authorization": "Bearer " + body["operational_token"]}
    assert client.get(f"/test/bind/{device_id}/" + "ab" * 32, headers=auth).status_code == 200
    assert client.get(f"/test/bind/{uuid4()}/" + "ab" * 32, headers=auth).status_code == 401
    assert client.get(f"/test/bind/{device_id}/" + "cd" * 32, headers=auth).status_code == 401


def test_operator_and_pending_cannot_issue(setup):
    from app.db.models import User
    from sqlalchemy import select

    client, admin, device_id = setup
    with get_session_factory()() as db:
        db.scalar(select(User).where(User.email == "admin@example.com")).role = "operator"
        db.commit()
    assert client.post(f"/api/adoption/{device_id}/enrollment", headers=admin).status_code == 403
    assert (
        client.post(f"/api/adoption/{device_id}/credentials/revoke", headers=admin).status_code
        == 403
    )
    with get_session_factory()() as db:
        db.scalar(select(User).where(User.email == "admin@example.com")).role = "admin"
        db.get(Repeater, device_id).status = "pending_adoption"
        db.commit()
    assert client.post(f"/api/adoption/{device_id}/enrollment", headers=admin).status_code == 409


def test_admin_only_and_reenrollment(setup):
    client, admin, device_id = setup
    assert client.post(f"/api/adoption/{device_id}/enrollment").status_code == 401
    old, _ = payload(setup)
    assert client.post("/enroll", json=old).status_code == 200
    new, _ = payload(setup)
    assert client.post("/enroll", json=new).status_code == 200
    assert (
        client.get(
            "/test/device", headers={"Authorization": "Bearer " + old["operational_token"]}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/test/device", headers={"Authorization": "Bearer " + new["operational_token"]}
        ).status_code
        == 200
    )
