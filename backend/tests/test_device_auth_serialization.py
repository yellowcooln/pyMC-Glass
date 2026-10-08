"""Body barriers and stale ORM objects must not preserve revoked authority."""

import asyncio
import inspect
import json

import httpx
import pytest
from app.api.routes.enrollment import enroll
from app.api.routes.inform import inform
from app.api.routes.inform_v2 import inform_v2
from app.db.models import CommandQueueItem, DeviceCredential, DeviceObservation, Repeater
from app.db.session import get_session_factory
from app.security.devices import get_current_device
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from test_device_auth import enrolled, legacy, v2  # noqa: F401
from test_device_enrollment import setup  # noqa: F401


@pytest.mark.parametrize("mutation", ["revoke", "reject"])
def test_auth_refreshes_stale_identity_map(enrolled, mutation):  # noqa: F811
    _, _, device_id, auth = enrolled
    with get_session_factory()() as stale:
        stale.get(Repeater, device_id)
        credential = stale.get(DeviceCredential, device_id)
        # Retain references: SQLAlchemy's identity map uses weak references.
        device = stale.get(Repeater, device_id)
        with get_session_factory()() as writer:
            if mutation == "reject":
                writer.get(Repeater, device_id).status = "rejected"
            else:
                from datetime import UTC, datetime

                writer.get(DeviceCredential, device_id).revoked_at = datetime.now(UTC)
            writer.commit()
        assert device.status == "adopted" and credential.revoked_at is None
        request = Request({"type": "http", "scheme": "https", "headers": [],
                           "server": ("testserver", 443), "path": "/inform"})
        token = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=auth["Authorization"].split()[1]
        )
        with pytest.raises(HTTPException) as denied:
            get_current_device(request, token, stale)
        assert denied.value.status_code == 401


@pytest.mark.parametrize("route", ["/inform", "/inform/v2"])
@pytest.mark.parametrize("mutation", ["credentials/revoke", "reject"])
def test_revocation_commits_while_body_is_paused(enrolled, route, mutation):  # noqa: F811
    client, admin, device_id, auth = enrolled
    with get_session_factory()() as db:
        item = CommandQueueItem(repeater_id=device_id, command="restart_service", status="queued")
        db.add(item)
        db.commit()
        command_id = item.id
    body = v2(device_id) if route.endswith("v2") else legacy()
    if route == "/inform":
        body["command_results"] = [{
            "command_id": command_id, "status": "success", "message": "stale",
            "completed_at": "2026-01-01T00:00:00Z",
        }]

    async def race():
        started, release = asyncio.Event(), asyncio.Event()

        async def stream():
            yield b" "
            started.set()
            await release.wait()
            yield json.dumps(body).encode()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="https://testserver"
        ) as peer:
            operation = asyncio.create_task(peer.post(route, content=stream(), headers=auth))
            try:
                await asyncio.wait_for(started.wait(), 5)
                revoked = await asyncio.wait_for(peer.post(
                    f"/api/adoption/{device_id}/{mutation}", json={}, headers=admin
                ), 5)
                assert revoked.status_code == 200
            finally:
                release.set()
            response = await asyncio.wait_for(operation, 5)
            assert response.status_code == 401

    asyncio.run(race())
    with get_session_factory()() as db:
        assert db.get(Repeater, device_id).last_inform_at is None
        assert db.get(Repeater, device_id).status == (
            "rejected" if mutation == "reject" else "adopted"
        )
        assert db.get(DeviceObservation, device_id) is None
        item = db.get(CommandQueueItem, command_id)
        assert item.status == "queued" and item.result_json is None


def test_blocking_transaction_handlers_use_fastapi_threadpool():
    assert not any(inspect.iscoroutinefunction(route) for route in (enroll, inform, inform_v2))


@pytest.mark.parametrize("mutation", ["credentials/revoke", "reject"])
def test_enrollment_body_pause_does_not_lock_or_preserve_approval(setup, mutation):  # noqa: F811
    from app.db.models import Certificate, DeviceEnrollment

    from test_device_enrollment import payload

    client, admin, device_id = setup
    body, _ = payload(setup)

    async def race():
        started, release = asyncio.Event(), asyncio.Event()

        async def stream():
            yield b" "
            started.set()
            await release.wait()
            yield json.dumps(body).encode()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="https://testserver"
        ) as peer:
            operation = asyncio.create_task(peer.post("/enroll", content=stream()))
            try:
                await asyncio.wait_for(started.wait(), 5)
                revoked = await asyncio.wait_for(peer.post(
                    f"/api/adoption/{device_id}/{mutation}", json={}, headers=admin
                ), 5)
                assert revoked.status_code == 200
            finally:
                release.set()
            assert (await asyncio.wait_for(operation, 5)).status_code == 401

    asyncio.run(race())
    with get_session_factory()() as db:
        assert db.get(DeviceCredential, device_id) is None
        assert db.get(DeviceEnrollment, device_id).consumed_at is not None
        from sqlalchemy import select

        assert db.scalar(select(Certificate)) is None


@pytest.mark.parametrize("route", ["/enroll", "/inform", "/inform/v2"])
def test_paused_sync_transaction_does_not_block_event_loop(enrolled, monkeypatch, route):  # noqa: F811
    import importlib
    from threading import Event

    from app.services.pki import PkiService

    from test_device_enrollment import payload

    client, admin, device_id, auth = enrolled
    started, release = Event(), Event()
    if route == "/enroll":
        body, _ = payload((client, admin, device_id))
        owner, method = PkiService, "validate_device_csr"
    else:
        body = v2(device_id) if route.endswith("v2") else legacy()
        owner = importlib.import_module("app.api.routes.inform_v2" if route.endswith("v2")
                                        else "app.api.routes.inform")
        method = "check_name_collision"
    original = getattr(owner, method)

    def paused(*args, **kwargs):
        started.set()
        assert release.wait(5), "Blocking transaction ran on the event loop"
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, staticmethod(paused) if route == "/enroll" else paused)

    async def race():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="https://testserver"
        ) as peer:
            operation = asyncio.create_task(peer.post(route, json=body, headers=auth))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                assert (await asyncio.wait_for(peer.get("/not-a-route"), 2)).status_code == 404
            finally:
                release.set()
            assert (await asyncio.wait_for(operation, 5)).status_code == 200

    asyncio.run(race())
