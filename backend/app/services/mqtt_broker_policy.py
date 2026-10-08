"""Pure bounded broker policy generation, without publication or activation.

Callers supply eligible identities and CA objects. This module never loads private
keys, queries a DB, writes files, reloads Mosquitto or claims revocation is effective.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa

from app.services.mqtt_identity import canonical_device_id

MAX_ACTIVE_DEVICES = 1024
MAX_REVOKED_CERTIFICATES = 4096
_SERIAL_HEX = re.compile(r"[0-9a-fA-F]{1,40}\Z")
SigningKey = (
    rsa.RSAPrivateKey
    | ec.EllipticCurvePrivateKey
    | dsa.DSAPrivateKey
    | ed25519.Ed25519PrivateKey
    | ed448.Ed448PrivateKey
)


def build_acl(active_device_ids: Iterable[str]) -> str:
    """Return sorted Mosquitto ACL text. Any invalid/duplicate input fails closed.

    Eligibility (adopted/connected/offline with non-revoked credentials) belongs
    to the future DB provider. There are no anonymous or global write patterns.
    """
    if active_device_ids is None or isinstance(active_device_ids, (str, bytes)):
        raise ValueError("active identities must be an iterable of device IDs")
    ids: set[str] = set()
    try:
        for index, device_id in enumerate(active_device_ids):
            if index >= MAX_ACTIVE_DEVICES:
                raise ValueError("too many active MQTT identities")
            canonical_device_id(device_id)
            if device_id in ids:
                raise ValueError("duplicate MQTT identity")
            ids.add(device_id)
    except TypeError as exc:
        raise ValueError("invalid active identities") from exc
    sections = ["user openhop-glass-backend\ntopic read glass/#"]
    sections.extend(
        f"user device:{item}\ntopic write glass/device:{item}/#" for item in sorted(ids)
    )
    return "\n\n".join(sections) + "\n"


def _utc_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("time must be aware UTC")
    if value < datetime(1950, 1, 1, tzinfo=UTC):
        raise ValueError("time outside X.509 range")
    return value.astimezone(UTC)


def build_crl(
    ca_certificate: x509.Certificate,
    ca_private_key: SigningKey,
    revoked: Iterable[tuple[str, datetime]],
    now: datetime,
) -> x509.CertificateRevocationList:
    """Sign a full CRL with serial-sorted entries; lifetime <=7 days and CA expiry.

    Serials are hex strings, positive and <=159 bits. Alternate hex spellings
    are accepted but duplicate numeric serials are rejected. No key path APIs.
    """
    now = _utc_time(now)
    if not isinstance(ca_certificate, x509.Certificate) or not isinstance(
        ca_private_key,
        (
            rsa.RSAPrivateKey,
            ec.EllipticCurvePrivateKey,
            dsa.DSAPrivateKey,
            ed25519.Ed25519PrivateKey,
            ed448.Ed448PrivateKey,
        ),
    ):
        raise ValueError("CA certificate and supported private key required")
    try:
        constraints = ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        usage = ca_certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("CA signing constraints required") from exc
    if (
        not constraints.critical
        or not constraints.value.ca
        or not usage.key_cert_sign
        or not usage.crl_sign
    ):
        raise ValueError("certificate is not a CRL-signing CA")
    if not ca_certificate.not_valid_before_utc <= now < ca_certificate.not_valid_after_utc:
        raise ValueError("CA is not currently valid")
    encoding = serialization.Encoding.DER
    key_format = serialization.PublicFormat.SubjectPublicKeyInfo
    if ca_certificate.public_key().public_bytes(
        encoding, key_format
    ) != ca_private_key.public_key().public_bytes(encoding, key_format):
        raise ValueError("CA certificate/private key mismatch")
    # X.509 timestamps have whole-second precision. Do not generate a zero-life CRL.
    last_update = now.replace(microsecond=0)
    try:
        next_update = min(now + timedelta(days=7), ca_certificate.not_valid_after_utc)
    except OverflowError:
        next_update = ca_certificate.not_valid_after_utc
    next_update = next_update.replace(microsecond=0)
    if next_update <= last_update:
        raise ValueError("CA expiry cannot accommodate CRL lifetime")
    if revoked is None or isinstance(revoked, (str, bytes)):
        raise ValueError("revocations must be an iterable of serial/time pairs")
    entries: dict[int, datetime] = {}
    try:
        for index, entry in enumerate(revoked):
            if index >= MAX_REVOKED_CERTIFICATES:
                raise ValueError("too many revoked certificates")
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("invalid revocation pair")
            serial_hex, revoked_at = entry
            if not isinstance(serial_hex, str) or not _SERIAL_HEX.fullmatch(serial_hex):
                raise ValueError("invalid certificate serial hex")
            serial = int(serial_hex, 16)
            if serial <= 0 or serial.bit_length() > 159 or serial in entries:
                raise ValueError("invalid or duplicate certificate serial")
            revoked_at = _utc_time(revoked_at)
            if revoked_at > now:
                raise ValueError("revocation time is in the future")
            entries[serial] = revoked_at
    except TypeError as exc:
        raise ValueError("invalid revocations") from exc
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_certificate.subject)
        .last_update(last_update)
        .next_update(next_update)
    )
    for serial, revoked_at in sorted(entries.items()):
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(serial)
            .revocation_date(revoked_at)
            .build()
        )
    algorithm = (
        None
        if isinstance(ca_private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey))
        else hashes.SHA256()
    )
    return builder.sign(ca_private_key, algorithm)
