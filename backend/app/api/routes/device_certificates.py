"""Authenticated, bounded CSR issuance; issuance is not node installation or broker proof."""

import asyncio
import hashlib
from collections.abc import Generator
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Certificate, DeviceCertificateRotation, DeviceCredential, Repeater
from app.db.session import get_db_session
from app.schemas.device_certificates import CertificateRenewalRequest, CertificateRenewalResponse
from app.security.devices import bind_device_identity, get_current_device, require_device_https
from app.services.audit import write_audit_log
from app.services.pki import PkiService

router = APIRouter()
RENEWAL_BODY_TIMEOUT_SECONDS = 10.0
_renewal_bearer = HTTPBearer(auto_error=False)


def _utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata; preserve identical public retry serialization.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def bounded_renewal(request: Request) -> CertificateRenewalRequest:
    require_device_https(request)
    data = bytearray()
    try:
        async with asyncio.timeout(RENEWAL_BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 16384:
                    raise HTTPException(413, "Certificate renewal body too large")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Certificate renewal body timed out") from None
    try:
        return CertificateRenewalRequest.model_validate_json(bytes(data))
    except ValidationError:
        raise HTTPException(422, "Invalid certificate renewal request") from None


def renewal_db(
    payload: CertificateRenewalRequest = Depends(bounded_renewal),
) -> Generator[Session, None, None]:
    # A dependency edge, not argument ordering: no session exists until the
    # bounded body has finished and passed schema validation.
    yield from get_db_session()


def renewal_device(
    request: Request,
    db: Session = Depends(renewal_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_renewal_bearer),
) -> Repeater:
    # Reuse the existing locked authority checks with this route's gated session.
    # Depending directly on get_current_device would bypass the body gate.
    return get_current_device(request=request, credentials=credentials, db=db)


def _public(rotation: DeviceCertificateRotation) -> CertificateRenewalResponse:
    return CertificateRenewalResponse(
        device_id=rotation.repeater_id,
        request_id=rotation.request_id,
        client_cert=rotation.client_cert_pem,
        ca_cert=rotation.ca_cert_pem,
        cert_serial=rotation.cert_serial,
        expires_at=_utc(rotation.expires_at),
        fingerprint_sha256=rotation.fingerprint_sha256,
    )


@router.post("/device/certificates/renew", response_model=CertificateRenewalResponse)
def renew_certificate(
    response: Response,
    device: Repeater = Depends(renewal_device),
    payload: CertificateRenewalRequest = Depends(bounded_renewal),
    db: Session = Depends(renewal_db),
) -> CertificateRenewalResponse:
    # get_current_device refreshes/locks parent then credential and rechecks bearer,
    # revocation and parent status. Same session keeps these locks through this commit.
    bind_device_identity(device, device_id=payload.device_id)
    pki = PkiService(get_settings())
    try:
        csr = pki.validate_device_csr(payload.csr_pem)
        key_hash = hashlib.sha256(
            csr.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).hexdigest()
    except ValueError:
        raise HTTPException(422, "Invalid CSR") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Certificate issuance unavailable") from None
    try:
        credential = db.scalar(
            select(DeviceCredential)
            .where(DeviceCredential.repeater_id == device.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        rotation = db.scalar(
            select(DeviceCertificateRotation)
            .where(DeviceCertificateRotation.repeater_id == device.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if credential is None or credential.revoked_at is not None:
            raise HTTPException(401, "Invalid device credential")
        now = datetime.now(UTC)
        if rotation is not None and rotation.credential_token_hash == credential.token_hash:
            if rotation.request_id == payload.request_id:
                cert = db.scalar(
                    select(Certificate).where(
                        Certificate.repeater_id == device.id,
                        Certificate.serial == rotation.cert_serial,
                    )
                )
                if (
                    rotation.csr_public_key_sha256 != key_hash
                    or credential.csr_public_key_sha256 != key_hash
                    or rotation.cert_serial != credential.cert_serial
                    or rotation.cert_serial != device.cert_serial
                    or cert is None
                    or cert.revoked_at is not None
                ):
                    raise HTTPException(409, "Stale or conflicting certificate renewal")
                public = _public(rotation)
                db.commit()
                response.headers["Cache-Control"] = "no-store"
                return public
            if rotation.node_reported_at is None and _utc(rotation.expires_at) > now:
                raise HTTPException(409, "Certificate renewal is pending")
        issued = pki.issue_device_certificate(device_id=device.id, csr_pem=payload.csr_pem)
        leaf = x509.load_pem_x509_certificate(issued.client_cert_pem.encode("ascii"))
        if rotation is None:
            rotation = DeviceCertificateRotation(repeater_id=device.id)
            db.add(rotation)
        rotation.request_id = payload.request_id
        rotation.credential_token_hash = credential.token_hash
        rotation.csr_public_key_sha256 = issued.csr_public_key_sha256
        rotation.previous_cert_serial = credential.cert_serial
        rotation.cert_serial = issued.serial
        rotation.client_cert_pem = issued.client_cert_pem
        rotation.ca_cert_pem = issued.ca_cert_pem
        rotation.issued_at = issued.issued_at
        rotation.expires_at = issued.expires_at
        rotation.fingerprint_sha256 = leaf.fingerprint(hashes.SHA256()).hex()
        rotation.node_reported_at = None
        rotation.node_reported_boot_id = None
        db.add(
            Certificate(
                repeater_id=device.id,
                serial=issued.serial,
                cn=issued.subject_cn,
                issued_at=issued.issued_at,
                expires_at=issued.expires_at,
                pem_hash=issued.pem_hash,
            )
        )
        credential.cert_serial = issued.serial
        credential.csr_public_key_sha256 = issued.csr_public_key_sha256
        device.cert_serial = issued.serial
        device.cert_expires_at = issued.expires_at
        write_audit_log(
            db,
            action="device_certificate_issued",
            target_type="repeater",
            target_id=device.id,
            details={"requester": "machine", "cert_serial": issued.serial},
        )
        public = _public(rotation)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Certificate renewal conflict") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Certificate issuance unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return public
