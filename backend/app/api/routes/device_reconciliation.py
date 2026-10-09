"""Bounded machine result reconciliation; an archive ACK is not execution proof."""

import asyncio
from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.v2.command import ResultAcceptanceV2, ResultV2
from app.contracts.v2.common import MAX_ENVELOPE_BYTES, decode_json
from app.db.models import Repeater
from app.db.session import get_db_session
from app.security.devices import bind_device_identity, get_current_device, require_device_https
from app.services.command_dispatch import reconcile_result

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


async def bounded_result(request: Request) -> ResultV2:
    require_device_https(request)
    data = bytearray()
    try:
        async with asyncio.timeout(10.0):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_ENVELOPE_BYTES:
                    raise HTTPException(413, "Result reconciliation body too large")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Result reconciliation body timed out") from None
    try:
        return ResultV2.model_validate(decode_json(bytes(data)))
    except ValueError:
        raise HTTPException(422, "Invalid result reconciliation envelope") from None


def reconciliation_db(
    payload: ResultV2 = Depends(bounded_result),
) -> Generator[Session, None, None]:
    # Explicit dependency edge: no session or authority lookup before the body gate.
    yield from get_db_session()


def reconciliation_device(
    request: Request,
    db: Session = Depends(reconciliation_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Repeater:
    return get_current_device(request=request, credentials=credentials, db=db)


@router.post("/device/commands/results/reconcile", response_model=ResultAcceptanceV2)
def reconcile_device_result(
    response: Response,
    payload: ResultV2 = Depends(bounded_result),
    device: Repeater = Depends(reconciliation_device),
    db: Session = Depends(reconciliation_db),
) -> ResultAcceptanceV2:
    try:
        bind_device_identity(device, device_id=str(payload.device_id))
        acceptance = reconcile_result(db, device.id, payload)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Result reconciliation conflict") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Result reconciliation unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return acceptance
