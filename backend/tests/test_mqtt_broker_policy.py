from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from app.services.mqtt_broker_policy import build_acl, build_crl
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 4, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def certificate(
    key,
    *,
    ca=True,
    crl_sign=True,
    key_cert_sign=True,
    before=NOW - timedelta(days=1),
    after=NOW + timedelta(days=30),
):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(before)
        .not_valid_after(after)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, key_cert_sign, crl_sign, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )


def test_acl_explicit_sorted_read_only_backend():
    expected = (
        "user openhop-glass-backend\ntopic read glass/#\n\n"
        f"user device:{A}\ntopic write glass/device:{A}/#\n\n"
        f"user device:{B}\ntopic write glass/device:{B}/#\n"
    )
    assert build_acl([B, A]) == expected
    assert build_acl(iter([A, B])) == expected
    assert build_acl([]) == "user openhop-glass-backend\ntopic read glass/#\n"
    assert "pattern" not in expected
    assert "topic write glass/#" not in expected


@pytest.mark.parametrize(
    "ids",
    [
        [A, A],
        ["device:" + A],
        [A.replace("-", "")],
        ["00000000-0000-4000-8000-00000000000A"],
        ["bad\ntopic write #"],
        [UUID(A)],
        [None],
        None,
    ],
)
def test_acl_invalid(ids):
    with pytest.raises(ValueError):
        build_acl(ids)


def test_acl_count_bound():
    assert build_acl(str(UUID(int=i)) for i in range(1024)).count("user device:") == 1024
    with pytest.raises(ValueError):
        build_acl(str(UUID(int=i)) for i in range(1025))


def test_real_crl_signed_and_serial_revoked(key):
    cert = certificate(key)
    revoked_at = NOW - timedelta(hours=1)
    crl = build_crl(cert, key, [("ff", revoked_at), ("1", NOW)], NOW)
    assert crl.issuer == cert.subject
    assert crl.is_signature_valid(cert.public_key())
    assert crl.last_update_utc == NOW
    assert crl.next_update_utc == NOW + timedelta(days=7)
    assert [row.serial_number for row in crl] == [1, 255]
    assert crl.get_revoked_certificate_by_serial_number(255).revocation_date_utc == revoked_at
    assert crl.get_revoked_certificate_by_serial_number(2) is None
    assert build_crl(cert, key, [], NOW).is_signature_valid(cert.public_key())
    short = certificate(key, after=NOW + timedelta(hours=1))
    assert build_crl(short, key, [], NOW).next_update_utc == short.not_valid_after_utc


@pytest.mark.parametrize(
    "serial", ["0", "-1", "+1", "0x1", "g", "", "1\n", " 1", 1, "8" + "0" * 39, "0" * 41]
)
def test_crl_invalid_serial(key, serial):
    with pytest.raises(ValueError):
        build_crl(certificate(key), key, [(serial, NOW)], NOW)


@pytest.mark.parametrize(
    "when",
    [
        NOW.replace(tzinfo=None),
        NOW + timedelta(seconds=1),
        NOW.astimezone(timezone(timedelta(hours=1))),
        None,
    ],
)
def test_crl_invalid_revocation_time(key, when):
    with pytest.raises(ValueError):
        build_crl(certificate(key), key, [("1", when)], NOW)


def test_crl_invalid_now_key_constraints_and_duplicates(key):
    cert = certificate(key)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError):
        build_crl(cert, wrong_key, [], NOW)
    for kwargs in (
        dict(ca=False),
        dict(crl_sign=False),
        dict(key_cert_sign=False),
        dict(after=NOW),
        dict(before=NOW + timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            build_crl(certificate(key, **kwargs), key, [], NOW)
    for now in (None, NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1)))):
        with pytest.raises(ValueError):
            build_crl(cert, key, [], now)
    with pytest.raises(ValueError):
        build_crl(cert, key, [("1", NOW), ("01", NOW)], NOW)
    with pytest.raises(ValueError):
        build_crl(cert, key, None, NOW)


def test_crl_count_bound(key):
    cert = certificate(key)
    assert len(build_crl(cert, key, ((format(i, "x"), NOW) for i in range(1, 4097)), NOW)) == 4096
    with pytest.raises(ValueError):
        build_crl(cert, key, ((format(i, "x"), NOW) for i in range(1, 4098)), NOW)
