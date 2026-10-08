"""Device bearer authority. Never derive identity or TLS from request headers."""

import re
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Certificate, DeviceCredential, DeviceEnrollment, Repeater
from app.db.session import get_db_session
from app.security.tokens import hash_token

ELIGIBLE_STATUSES = frozenset({"adopted", "connected", "offline"})
_device_bearer = HTTPBearer(auto_error=False)


def require_device_https(request: Request) -> None:
    # Only ASGI scheme is accepted. Trusted proxy configuration is deployment-owned.
    if request.url.scheme != "https":
        raise HTTPException(400, "HTTPS required")


def lock_repeater(db: Session, repeater_id: str) -> Repeater | None:
    """Common serialization point; acquire before credential/enrollment rows.

    Refresh the identity map after waiting: a previously loaded object is not
    authority. The caller holds this lock through commit/rollback, never streaming.
    """
    return db.scalar(
        select(Repeater)
        .where(Repeater.id == repeater_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def get_current_device(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_device_bearer),
    db: Session = Depends(get_db_session),
) -> Repeater:
    require_device_https(request)
    if credentials is None or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", credentials.credentials):
        raise HTTPException(401, "Invalid device credential")
    token_hash = hash_token(credentials.credentials)
    # Lookup only: do not lock the credential before its parent repeater.
    repeater_id = db.scalar(
        select(DeviceCredential.repeater_id).where(DeviceCredential.token_hash == token_hash)
    )
    if repeater_id is None:
        raise HTTPException(401, "Invalid device credential")
    device = lock_repeater(db, repeater_id)
    credential = db.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.repeater_id == repeater_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        device is None
        or credential is None
        or credential.token_hash != token_hash
        or credential.revoked_at is not None
        or device.status not in ELIGIBLE_STATUSES
    ):
        raise HTTPException(401, "Invalid device credential")
    return device


def bind_device_identity(device: Repeater, *, device_id: str, pubkey: str | None = None) -> None:
    """Bind a decoded operational body, not a mutable display name."""
    if device.id != device_id or (pubkey is not None and device.pubkey != pubkey):
        raise HTTPException(401, "Device identity mismatch")


def revoke_device_credentials(db: Session, repeater_id: str) -> None:
    """Caller owns commit; lock parent before invalidating credential/certificate rows."""
    if db.scalar(select(Repeater.id).where(Repeater.id == repeater_id).with_for_update()) is None:
        raise HTTPException(404, "Repeater not found")
    now = datetime.now(UTC)
    db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.repeater_id == repeater_id)
        .values(revoked_at=now)
    )
    db.execute(
        update(DeviceEnrollment)
        .where(DeviceEnrollment.repeater_id == repeater_id)
        .values(consumed_at=now)
    )
    db.execute(
        update(Certificate)
        .where(Certificate.repeater_id == repeater_id, Certificate.revoked_at.is_(None))
        .values(revoked_at=now)
    )
