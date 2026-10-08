"""Explicit enrollment for operational API tests; no auth dependency overrides."""

import secrets

from app.db.models import Repeater
from app.db.session import get_session_factory
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def enroll_device(client, repeater_id, admin_headers):
    client.base_url = "https://testserver"
    with get_session_factory()() as db:
        repeater = db.get(Repeater, repeater_id)
        node_name, pubkey = repeater.node_name, repeater.pubkey
    approval = client.post(f"/api/adoption/{repeater_id}/enrollment", headers=admin_headers)
    assert approval.status_code == 200
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_name)]))
        .sign(key, hashes.SHA256())
    )
    operational_token = secrets.token_urlsafe(32)
    response = client.post(
        "/enroll",
        json={
            "device_id": repeater_id,
            "node_name": node_name,
            "pubkey": pubkey,
            "enrollment_token": approval.json()["enrollment_token"],
            "operational_token": operational_token,
            "csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode(),
        },
    )
    assert response.status_code == 200
    assert "client_key" not in response.json()
    cert = x509.load_pem_x509_certificate(response.json()["client_cert"].encode())
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    # Subsequent operational requests use this actual issued credential. Admin API
    # requests in these tests already pass their own explicit admin headers.
    client.headers["Authorization"] = "Bearer " + operational_token
    return response
