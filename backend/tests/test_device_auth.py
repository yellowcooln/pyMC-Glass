"""Operational inform authority regressions through real HTTPS enrollment."""

import json
from uuid import uuid4

import pytest
from app.db.models import CommandQueueItem, DeviceCredential, Repeater
from app.db.session import get_session_factory
from sqlalchemy import select

from test_device_enrollment import payload, setup  # noqa: F401, F811
from test_inform_control_plane_api import _inform_payload


@pytest.fixture
def enrolled(setup):  # noqa: F811 (pytest fixture imported for real enrollment)
    client, admin, device_id = setup
    body, _ = payload(setup)
    assert client.post("/enroll", json=body).status_code == 200
    return client, admin, device_id, {"Authorization": "Bearer " + body["operational_token"]}


def legacy():
    return dict(_inform_payload("node-one"), pubkey="ab" * 32)


def v2(device_id):
    return {
        "version": 2,
        "type": "inform",
        "device_id": device_id,
        "boot_id": str(uuid4()),
        "sent_at": "2026-01-01T00:00:00Z",
        "node_name": "node-one",
        "software_version": "2.0",
        "capabilities": {"telemetry": 1},
        "inventory": {"radios": [], "identities": [], "sensors": [], "plugins": []},
        "telemetry": {"cpu": 12},
        "results": [],
    }


@pytest.mark.parametrize("status", ["adopted", "connected", "offline", "rejected"])
def test_anonymous_trusted_node_cannot_mutate_or_dispatch(enrolled, status):
    client, _, device_id, _ = enrolled
    with get_session_factory()() as db:
        db.get(Repeater, device_id).status = status
        db.add(CommandQueueItem(repeater_id=device_id, command="restart_service", status="queued"))
        db.commit()
    response = client.post("/inform", json=legacy(), headers={"X-Client-Cert": "trusted"})
    assert response.status_code == 401
    with get_session_factory()() as db:
        assert db.get(Repeater, device_id).last_inform_at is None
        assert db.scalar(select(CommandQueueItem)).status == "queued"


def test_binding_rename_collisions_and_no_private_keys(enrolled):
    client, _, device_id, auth = enrolled
    assert (
        client.post(
            "/inform", json=legacy(), headers={"Authorization": "Bearer " + "x" * 43}
        ).status_code
        == 401
    )
    assert (
        client.post("/inform", json=dict(legacy(), pubkey="cd" * 32), headers=auth).status_code
        == 401
    )
    with get_session_factory()() as db:
        db.get(Repeater, device_id).node_name = "renamed"
        db.add(Repeater(node_name="other", pubkey="cd" * 32, status="adopted"))
        db.commit()
    response = client.post("/inform", json=legacy(), headers=auth)
    assert response.status_code == 200
    assert "client_key" not in response.text and response.json()["type"] != "cert_renewal"
    assert (
        client.post("/inform", json=dict(legacy(), node_name="other"), headers=auth).status_code
        == 409
    )
    with get_session_factory()() as db:
        assert db.get(Repeater, device_id).node_name == "renamed"


def test_pending_results_and_identity_collisions(client):
    body = legacy()
    assert client.post("/inform", json=body).status_code == 200
    result = {
        "command_id": str(uuid4()),
        "status": "success",
        "message": "ok",
        "completed_at": "2026-01-01T00:00:00Z",
    }
    assert client.post("/inform", json=dict(body, command_results=[result])).status_code == 401
    assert client.post("/inform", json=dict(body, pubkey="cd" * 32)).status_code == 409
    assert client.post("/inform", json=dict(body, node_name="other")).status_code == 409


def test_v2_persists_observation_only_and_reject_revokes(enrolled):
    client, admin, device_id, auth = enrolled
    body = v2(device_id)
    response = client.post("/inform/v2", json=body, headers=auth)
    assert response.status_code == 200
    assert response.json()["boot_id"] == body["boot_id"]
    for field in ["accepted_results", "jobs", "queries"]:
        assert response.json()[field] == []
    from app.db.models import DeviceObservation

    with get_session_factory()() as db:
        row = db.get(DeviceObservation, device_id)
        assert json.loads(row.capabilities_json) == body["capabilities"]
        assert json.loads(row.inventory_json) == body["inventory"]
        assert json.loads(row.telemetry_json) == body["telemetry"]
    assert (
        client.post(f"/api/adoption/{device_id}/reject", json={}, headers=admin).status_code == 200
    )
    with get_session_factory()() as db:
        assert db.get(DeviceCredential, device_id).revoked_at is not None
    assert (
        client.post(f"/api/adoption/{device_id}/adopt", json={}, headers=admin).status_code == 200
    )
    assert client.post("/inform/v2", json=body, headers=auth).status_code == 401
    assert client.post("/inform", json=legacy(), headers=auth).status_code == 401


def test_v2_auth_identity_https_and_bounds(enrolled):
    client, _, device_id, auth = enrolled
    body = v2(device_id)
    assert client.post("/inform/v2", json=body).status_code == 401
    assert (
        client.post(
            "/inform/v2", json=body, headers={"X-Device-Id": device_id, "X-Client-Cert": "trusted"}
        ).status_code
        == 401
    )
    assert (
        client.post("/inform/v2", json=dict(body, device_id=str(uuid4())), headers=auth).status_code
        == 401
    )
    assert (
        client.post(
            "/inform/v2", json=dict(body, pubkey="0x" + "cd" * 32), headers=auth
        ).status_code
        == 401
    )
    for version in [True, "2", 1]:
        assert (
            client.post("/inform/v2", json=dict(body, version=version), headers=auth).status_code
            == 422
        )
    assert (
        client.post(
            "/inform/v2", json=dict(body, capabilities={"telemetry": True}), headers=auth
        ).status_code
        == 422
    )
    for route in ["/inform", "/inform/v2"]:
        assert (
            client.post(
                "http://testserver" + route,
                json=body if route.endswith("v2") else legacy(),
                headers=dict(auth, **{"X-Forwarded-Proto": "https"}),
            ).status_code
            == 400
        )
        assert client.post(route, content=b"x" * (256 * 1024 + 1), headers=auth).status_code == 413
    with get_session_factory()() as db:
        db.get(Repeater, device_id).status = "offline"
        db.commit()
    assert client.post("/inform/v2", json=body, headers=auth).status_code == 200


def test_v2_results_are_not_acked_or_dispatched_and_latest_observation_updates(enrolled):
    from app.contracts.v2.telemetry import ResponseV2
    from app.db.models import DeviceObservation

    client, _, device_id, auth = enrolled
    body = v2(device_id)
    with get_session_factory()() as db:
        db.add(CommandQueueItem(repeater_id=device_id, command="restart_service", status="queued"))
        db.commit()
    body["results"] = [
        {
            "version": 2,
            "type": "result",
            "device_id": device_id,
            "boot_id": body["boot_id"],
            "sent_at": body["sent_at"],
            "request_id": str(uuid4()),
            "execution_id": None,
            "status": "succeeded",
            "completed_at": body["sent_at"],
            "details": {"unpersisted": True},
        }
    ]
    response = client.post("/inform/v2", json=body, headers=auth)
    assert response.status_code == 200
    parsed = ResponseV2.model_validate(response.json())
    assert not parsed.accepted_results and not parsed.jobs and not parsed.queries
    body.update(boot_id=str(uuid4()), telemetry={"cpu": 99}, capabilities={"telemetry": 2})
    with get_session_factory()() as db:
        db.get(Repeater, device_id).node_name = "renamed"
        db.commit()
    assert client.post("/inform/v2", json=body, headers=auth).status_code == 200
    with get_session_factory()() as db:
        assert db.scalar(select(CommandQueueItem)).status == "queued"
        assert db.scalar(select(CommandQueueItem)).result_json is None
        row = db.get(DeviceObservation, device_id)
        assert row.boot_id == body["boot_id"]
        assert json.loads(row.telemetry_json) == {"cpu": 99}
        assert json.loads(row.capabilities_json) == {"telemetry": 2}
        assert db.get(Repeater, device_id).node_name == "renamed"
        db.add(Repeater(node_name="occupied", pubkey="cd" * 32, status="adopted"))
        db.commit()
    assert (
        client.post("/inform/v2", json=dict(body, node_name="occupied"), headers=auth).status_code
        == 409
    )


@pytest.mark.parametrize("route", ["/inform", "/inform/v2"])
def test_decoder_duplicate_fields_depth_and_stream_bound(enrolled, route):
    client, _, device_id, auth = enrolled
    body = v2(device_id) if route.endswith("v2") else legacy()
    encoded = json.dumps(body)
    duplicate = encoded[:-1] + ', "node_name":"forged"}'
    assert client.post(route, content=duplicate, headers=auth).status_code == 422
    deep = "[" * 20 + "0" + "]" * 20
    assert client.post(route, content='{"deep":' + deep + "}", headers=auth).status_code == 422
    assert (
        client.post(route, content=iter([b" " * 131072, b" " * 131073]), headers=auth).status_code
        == 413
    )


def test_anonymous_results_cannot_complete_adopted_queue(enrolled):
    client, _, device_id, _ = enrolled
    with get_session_factory()() as db:
        item = CommandQueueItem(repeater_id=device_id, command="restart_service", status="queued")
        db.add(item)
        db.commit()
        command_id = item.id
    body = legacy()
    body["command_results"] = [
        {
            "command_id": command_id,
            "status": "success",
            "message": "forged",
            "completed_at": "2026-01-01T00:00:00Z",
        }
    ]
    assert client.post("/inform", json=body).status_code == 401
    with get_session_factory()() as db:
        assert db.get(CommandQueueItem, command_id).status == "queued"
        assert db.get(CommandQueueItem, command_id).result_json is None


def test_invalid_auth_cannot_fall_back_to_anonymous_pending(client):
    client.base_url = "https://testserver"
    auth = {"Authorization": "Bearer " + "x" * 43}
    assert client.post("/inform", json=legacy(), headers=auth).status_code == 401
    with get_session_factory()() as db:
        assert db.scalar(select(Repeater)) is None
