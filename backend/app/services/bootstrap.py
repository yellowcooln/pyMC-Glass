from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import BootstrapClaim, User


class BootstrapAlreadyCompleted(Exception):
    """The one-time administrator bootstrap is no longer available."""


def claim_first_admin(db: Session) -> None:
    """Claim bootstrap in the caller's transaction, before any user is created.

    The fixed primary key serializes competing API/seed transactions. The claim,
    user and audit must be committed together; this helper never commits.
    """
    db.add(BootstrapClaim(id=1))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BootstrapAlreadyCompleted from exc

    if db.scalar(select(func.count()).select_from(User)):
        db.rollback()
        raise BootstrapAlreadyCompleted
