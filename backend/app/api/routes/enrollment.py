"""One-time administrator approval and node-owned CSR enrollment."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Certificate, DeviceCredential, DeviceEnrollment, Repeater, User
from app.db.session import get_db_session
from app.schemas.device_enrollment import (
    EnrollmentRequest,
    EnrollmentResponse,
    EnrollmentTokenResponse,
)
from app.security.deps import require_roles
from app.security.devices import (
    ELIGIBLE_STATUSES,
    lock_repeater,
    require_device_https,
    revoke_device_credentials,
)
from app.security.tokens import hash_token
from app.services.audit import write_audit_log
from app.services.pki import PkiService

router = APIRouter()


def _locked_repeater(db: Session, repeater_id: str) -> Repeater | None:
    return lock_repeater(db, repeater_id)


@router.post("/api/adoption/{repeater_id}/enrollment", response_model=EnrollmentTokenResponse)
def create_enrollment(
    repeater_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
    user: User = Depends(require_roles("admin")),
) -> EnrollmentTokenResponse:
    require_device_https(request)
    repeater = _locked_repeater(db, repeater_id)
    if repeater is None:
        raise HTTPException(404, "Repeater not found")
    if repeater.status not in ELIGIBLE_STATUSES:
        raise HTTPException(409, "Repeater is not adopted")
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    enrollment = db.scalar(
        select(DeviceEnrollment)
        .where(DeviceEnrollment.repeater_id == repeater_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if enrollment is None:
        enrollment = DeviceEnrollment(repeater_id=repeater_id)
        db.add(enrollment)
    enrollment.token_hash = hash_token(raw_token)
    enrollment.expected_node_name = repeater.node_name
    enrollment.expected_pubkey = repeater.pubkey
    enrollment.expires_at = expires_at
    enrollment.consumed_at = None
    write_audit_log(
        db,
        action="device_enrollment_issued",
        target_type="repeater",
        target_id=repeater_id,
        user_id=user.id,
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return EnrollmentTokenResponse(
        device_id=repeater_id, enrollment_token=raw_token, expires_at=expires_at
    )


@router.post("/api/adoption/{repeater_id}/credentials/revoke")
def revoke_credential(
    repeater_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    user: User = Depends(require_roles("admin")),
) -> dict[str, str]:
    require_device_https(request)
    if _locked_repeater(db, repeater_id) is None:
        raise HTTPException(404, "Repeater not found")
    revoke_device_credentials(db, repeater_id)
    write_audit_log(
        db,
        action="device_credentials_revoked",
        target_type="repeater",
        target_id=repeater_id,
        user_id=user.id,
    )
    db.commit()
    return {"device_id": repeater_id, "status": "revoked"}


async def bounded_enrollment(request: Request) -> EnrollmentRequest:
    require_device_https(request)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 16384:
            raise HTTPException(413, "Enrollment body too large")
        data.extend(chunk)
    try:
        return EnrollmentRequest.model_validate_json(bytes(data))
    except ValidationError:
        # FastAPI's standard validation response includes input values: never echo secrets.
        raise HTTPException(422, "Invalid enrollment request") from None


@router.post("/enroll", response_model=EnrollmentResponse)
def enroll(
    response: Response,
    payload: EnrollmentRequest = Depends(bounded_enrollment),
    db: Session = Depends(get_db_session),
) -> EnrollmentResponse:
    repeater = _locked_repeater(db, payload.device_id)
    credential = db.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.repeater_id == payload.device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    enrollment = db.scalar(
        select(DeviceEnrollment)
        .where(DeviceEnrollment.repeater_id == payload.device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        repeater is None
        or enrollment is None
        or repeater.status not in ELIGIBLE_STATUSES
        or enrollment.expected_node_name != payload.node_name
        or enrollment.expected_pubkey != payload.pubkey
        or repeater.node_name != payload.node_name
        or repeater.pubkey != payload.pubkey
        or enrollment.token_hash != hash_token(payload.enrollment_token)
        or enrollment.consumed_at is not None
    ):
        raise HTTPException(401, "Invalid enrollment approval")
    pki = PkiService(get_settings())
    try:
        pki.validate_device_csr(payload.csr_pem)
    except ValueError:
        raise HTTPException(422, "Invalid CSR") from None
    # Evaluate the deadline after any row-lock wait and CSR validation.
    now = datetime.now(UTC)
    consumed = db.execute(
        update(DeviceEnrollment)
        .where(
            DeviceEnrollment.repeater_id == payload.device_id,
            DeviceEnrollment.token_hash == hash_token(payload.enrollment_token),
            DeviceEnrollment.consumed_at.is_(None),
            DeviceEnrollment.expires_at > now,
            DeviceEnrollment.expected_node_name == payload.node_name,
            DeviceEnrollment.expected_pubkey == payload.pubkey,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "Invalid enrollment approval")
    try:
        issued = pki.issue_device_certificate(device_id=payload.device_id, csr_pem=payload.csr_pem)
        db.execute(
            update(Certificate)
            .where(Certificate.repeater_id == payload.device_id, Certificate.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        if credential is None:
            credential = DeviceCredential(repeater_id=payload.device_id)
            db.add(credential)
        new_hash = hash_token(payload.operational_token)
        if credential.token_hash == new_hash:
            db.rollback()
            raise HTTPException(409, "A new operational credential is required")
        credential.token_hash = new_hash
        credential.csr_public_key_sha256 = issued.csr_public_key_sha256
        credential.cert_serial = issued.serial
        credential.created_at = now
        credential.revoked_at = None
        db.add(
            Certificate(
                repeater_id=payload.device_id,
                serial=issued.serial,
                cn=issued.subject_cn,
                issued_at=issued.issued_at,
                expires_at=issued.expires_at,
                pem_hash=issued.pem_hash,
            )
        )
        repeater.cert_serial = issued.serial
        repeater.cert_expires_at = issued.expires_at
        db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Credential conflict") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Certificate issuance unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return EnrollmentResponse(
        device_id=payload.device_id,
        client_cert=issued.client_cert_pem,
        ca_cert=issued.ca_cert_pem,
        cert_serial=issued.serial,
        expires_at=issued.expires_at,
    )
