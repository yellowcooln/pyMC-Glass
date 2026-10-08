import json
from datetime import UTC, datetime

import pytest
from app.db.models import (
    DeviceCredential,
    MqttIngestEvent,
    Packet,
    Repeater,
    TopologyNode,
    TopologyObservation,
    TopologyObservationSample,
    TopologyRollupHourly,
)
from app.db.session import get_session_factory
from app.services.mqtt_ingest import MqttIngestProcessor, _coerce_optional_float
from app.services.telemetry_stream import get_mqtt_telemetry_broadcaster
from sqlalchemy import select


def _bootstrap_admin(client) -> None:
    created = client.post(
        "/api/bootstrap/admin",
        json={
            "email": "admin@example.com",
            "password": "verysecurepassword123",
            "display_name": "Admin",
        },
    )
    assert created.status_code == 200


def _login(client) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "verysecurepassword123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


DEVICE_A = "11111111-1111-4111-8111-111111111111"
DEVICE_B = "22222222-2222-4222-8222-222222222222"


def _enroll(device_id=DEVICE_A, *, status="connected", credential=True, revoked=False):
    # Synthetic persisted enrollment, not an authentication mock or bearer presented to MQTT.
    with get_session_factory()() as db:
        db.add(Repeater(id=device_id, node_name=f"db-{device_id}", pubkey=device_id, status=status))
        db.flush()
        if credential:
            db.add(DeviceCredential(
                repeater_id=device_id, token_hash=device_id.replace("-", "") * 2,
                csr_public_key_sha256="a" * 64, cert_serial=device_id.replace("-", ""),
                revoked_at=datetime.now(UTC) if revoked else None,
            ))
        db.commit()


def _envelope(device_id=DEVICE_A, kind="packet"):
    topic = f"glass/device:{device_id}/{kind}"
    if kind == "event":
        topic += "/noise_floor"
    envelope = {
        "version": 2, "type": kind, "device_id": device_id, "topic": topic,
        "node_name": "untrusted-display", "timestamp": "2026-04-15T12:30:45Z",
        "payload": {"type": 2, "route": 0, "rssi": -81.5, "snr": 8.5,
                    "src_hash": "AA", "dst_hash": "BB", "payload": "abcd",
                    "packet_hash": "ABCDEF1234567890"},
    }
    if kind == "event":
        envelope["event_name"] = "noise_floor"
        envelope["payload"] = {"noise_floor_dbm": -111.2}
    if kind == "advert":
        envelope["payload"] = {
            "pubkey": "ABCD", "node_name": "observed-neighbor", "is_repeater": True,
            "zero_hop": True, "route_type": 0, "rssi": -81.5, "snr": 8.5,
            "latitude": 51.5, "longitude": -0.1, "advert_count": 3,
        }
    return topic, envelope


def _send(processor, topic, envelope):
    return processor.process_message_with_event(topic, json.dumps(envelope).encode())


def _assert_no_analytics():
    with get_session_factory()() as db:
        for model in (MqttIngestEvent, Packet, TopologyNode, TopologyObservation,
                      TopologyObservationSample, TopologyRollupHourly):
            assert db.scalars(select(model)).all() == []


def test_mqtt_packet_ingest_and_dedup(client):
    _enroll()
    _enroll(DEVICE_B)
    processor = MqttIngestProcessor(get_session_factory())
    topic, envelope = _envelope()
    envelope["node_name"] = f"db-{DEVICE_B}"
    first = _send(processor, topic, envelope)
    envelope["node_name"] = "renamed-display"
    second = _send(processor, topic, envelope)
    assert first.status == "ingested"
    assert second.status == "duplicate"
    assert first.telemetry_event["repeater_id"] == DEVICE_A
    assert first.telemetry_event["node_name"] == f"db-{DEVICE_A}"
    with get_session_factory()() as db:
        events = db.scalars(select(MqttIngestEvent)).all()
        packets = db.scalars(select(Packet)).all()
        assert len(events) == len(packets) == 1
        assert packets[0].packet_hash == "ABCDEF1234567890"
        assert packets[0].repeater_id == DEVICE_A
        assert packets[0].rssi == -81.5
        assert packets[0].snr == 8.5
        assert db.get(Repeater, DEVICE_A).node_name == f"db-{DEVICE_A}"


@pytest.mark.parametrize("status", ["adopted", "connected", "offline"])
def test_mqtt_event_eligible_statuses(client, status):
    _enroll(status=status)
    topic, envelope = _envelope(kind="event")
    result = _send(MqttIngestProcessor(get_session_factory()), topic, envelope)
    assert result.status == "ingested"
    assert result.telemetry_event["event_name"] == "noise_floor"
    with get_session_factory()() as db:
        row = db.scalar(select(MqttIngestEvent))
        assert row.repeater_id == DEVICE_A
        assert json.loads(row.payload_json) == {"noise_floor_dbm": -111.2}
        assert db.scalars(select(Packet)).all() == []


def test_mqtt_advert_retains_topology_analytics(client):
    _enroll()
    topic, envelope = _envelope(kind="advert")
    processor = MqttIngestProcessor(get_session_factory())
    assert _send(processor, topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        rollup = db.scalar(select(TopologyRollupHourly))
        assert rollup.observed_nodes == rollup.zero_hop_nodes == 1
        assert rollup.avg_rssi == -81.5
        assert rollup.avg_snr == 8.5
    envelope["timestamp"] = "2026-04-15T12:31:45Z"
    envelope["payload"]["advert_count"] = 4
    assert _send(processor, topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        node = db.scalar(select(TopologyNode))
        observation = db.scalar(select(TopologyObservation))
        rollup = db.scalar(select(TopologyRollupHourly))
        assert node.pubkey == "abcd"
        assert node.node_name == "observed-neighbor"
        assert node.latitude == 51.5
        assert observation.observer_repeater_id == DEVICE_A
        assert observation.advert_count == 4
        assert len(db.scalars(select(TopologyObservationSample)).all()) == 2
        assert rollup.observed_nodes == rollup.zero_hop_nodes == 1
        assert rollup.avg_rssi == -81.5


def test_mqtt_hourly_rollup_includes_two_distinct_neighbors(client):
    _enroll()
    processor = MqttIngestProcessor(get_session_factory())
    topic, envelope = _envelope(kind="advert")
    assert _send(processor, topic, envelope).status == "ingested"
    envelope["payload"].update(pubkey="EFGH", rssi=-91.5, snr=4.5)
    assert _send(processor, topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        assert len(db.scalars(select(TopologyObservation)).all()) == 2
        rollup = db.scalar(select(TopologyRollupHourly))
        assert rollup.observed_nodes == rollup.zero_hop_nodes == 2
        assert rollup.avg_rssi == -86.5
        assert rollup.avg_snr == 6.5


@pytest.mark.parametrize("value", [
    "NaN", "Infinity", "+Infinity", "-Infinity", "1e9999", "-1e9999",
    True, False, 10**400,
], ids=["nan", "inf", "positive-inf", "negative-inf", "overflow", "negative-overflow",
        "true", "false", "huge-int"])
@pytest.mark.parametrize("kind", ["packet", "advert"])
def test_mqtt_invalid_numeric_metrics_are_unknown_and_raw_payload_retained(client, value, kind):
    # SQLite turns NaN into NULL, so also verify coercion before DB adaptation.
    assert _coerce_optional_float(value) is None
    _enroll()
    topic, envelope = _envelope(kind=kind)
    envelope["payload"].update(rssi=value, snr=value)
    if kind == "advert":
        envelope["payload"].update(latitude=value, longitude=value)
    result = _send(MqttIngestProcessor(get_session_factory()), topic, envelope)
    assert result.status == "ingested"
    assert result.telemetry_event["payload"] == envelope["payload"]
    with get_session_factory()() as db:
        assert json.loads(db.scalar(select(MqttIngestEvent)).payload_json) == envelope["payload"]
        if kind == "packet":
            packet = db.scalar(select(Packet))
            assert packet.rssi is None
            assert packet.snr is None
        else:
            for model in (TopologyObservation, TopologyObservationSample):
                row = db.scalar(select(model))
                assert row.rssi is None
                assert row.snr is None
            for model in (TopologyNode, TopologyObservation):
                row = db.scalar(select(model))
                assert row.latitude is None
                assert row.longitude is None
            rollup = db.scalar(select(TopologyRollupHourly))
            assert rollup.avg_rssi is None
            assert rollup.avg_snr is None


@pytest.mark.parametrize("kind", ["packet", "advert"])
def test_mqtt_finite_numeric_strings_remain_supported(client, kind):
    _enroll()
    topic, envelope = _envelope(kind=kind)
    envelope["payload"].update(rssi="-81.5", snr="8.5")
    if kind == "advert":
        envelope["payload"].update(latitude="51.5", longitude="-0.1")
    assert _send(MqttIngestProcessor(get_session_factory()), topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        row = db.scalar(select(Packet if kind == "packet" else TopologyObservation))
        assert row.rssi == -81.5
        assert row.snr == 8.5
        if kind == "advert":
            assert row.latitude == 51.5
            assert row.longitude == -0.1


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e9999", False])
def test_mqtt_noise_event_keeps_raw_value_without_creating_metrics(client, value):
    # Events retain bounded raw JSON only; no typed noise metric is persisted here.
    _enroll()
    topic, envelope = _envelope(kind="event")
    envelope["payload"]["noise_floor_dbm"] = value
    assert _send(MqttIngestProcessor(get_session_factory()), topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        assert json.loads(db.scalar(select(MqttIngestEvent)).payload_json) == envelope["payload"]
        assert db.scalars(select(Packet)).all() == []
        assert db.scalars(select(TopologyObservation)).all() == []


@pytest.mark.parametrize("kind", ["packet", "advert", "event"])
@pytest.mark.parametrize("mismatch", ["topic_b", "body_b", "envelope_topic_b"])
def test_mqtt_identity_mismatch_denied_without_mutation(client, kind, mismatch):
    _enroll()
    _enroll(DEVICE_B)
    topic, envelope = _envelope(kind=kind)
    if mismatch == "topic_b":
        topic = topic.replace(DEVICE_A, DEVICE_B)
    elif mismatch == "body_b":
        envelope["device_id"] = DEVICE_B
    else:
        envelope["topic"] = topic.replace(DEVICE_A, DEVICE_B)
    result = _send(MqttIngestProcessor(get_session_factory()), topic, envelope)
    assert result.status == "invalid"
    assert result.telemetry_event is None
    _assert_no_analytics()


@pytest.mark.parametrize("status,credential,revoked", [
    ("pending", True, False), ("rejected", True, False), ("revoked", True, False),
    ("connected", False, False), ("connected", True, True),
])
@pytest.mark.parametrize("kind", ["packet", "advert", "event"])
def test_mqtt_ineligible_denied_without_mutation(client, status, credential, revoked, kind):
    _enroll(status=status, credential=credential, revoked=revoked)
    topic, envelope = _envelope(kind=kind)
    result = _send(MqttIngestProcessor(get_session_factory()), topic, envelope)
    assert result.status == "unauthorized_repeater"
    assert result.telemetry_event is None
    _assert_no_analytics()


def test_mqtt_unknown_stable_id_denied(client):
    topic, envelope = _envelope()
    result = _send(MqttIngestProcessor(get_session_factory()), topic, envelope)
    assert result.status == "unknown_repeater"
    _assert_no_analytics()


@pytest.mark.parametrize("kind", ["packet", "advert", "event"])
@pytest.mark.parametrize("legacy", ["v1", "raw", "name_topic"])
def test_mqtt_all_legacy_denied(client, kind, legacy):
    _enroll()
    topic, envelope = _envelope(kind=kind)
    if legacy == "v1":
        envelope["version"] = 1
        del envelope["device_id"]
    elif legacy == "raw":
        envelope = envelope["payload"]
    else:
        topic = topic.replace(f"device:{DEVICE_A}", f"db-{DEVICE_A}")
        envelope["topic"] = topic
    assert _send(MqttIngestProcessor(get_session_factory()), topic, envelope).status == "invalid"
    _assert_no_analytics()


@pytest.mark.parametrize("payload", [b"{", b"\xff", b"x" * (256 * 1024 + 1),
                                     b'{"version":2,"version":2}', b'{"x":NaN}'])
def test_mqtt_malformed_denied(client, payload):
    _enroll()
    topic, _ = _envelope()
    result = MqttIngestProcessor(get_session_factory()).process_message_with_event(topic, payload)
    assert result.status == "invalid"
    assert result.telemetry_event is None
    _assert_no_analytics()


def test_mqtt_revocation_rechecked_before_dedup(client):
    _enroll()
    processor = MqttIngestProcessor(get_session_factory())
    topic, envelope = _envelope()
    assert _send(processor, topic, envelope).status == "ingested"
    with get_session_factory()() as db:
        credential = db.get(DeviceCredential, DEVICE_A)
        credential.revoked_at = datetime.now(UTC)
        db.commit()
    result = _send(processor, topic, envelope)
    assert result.status == "unauthorized_repeater"
    assert result.telemetry_event is None
    with get_session_factory()() as db:
        assert len(db.scalars(select(MqttIngestEvent)).all()) == 1
        assert len(db.scalars(select(Packet)).all()) == 1


def test_mqtt_refreshes_cached_authority(client):
    _enroll()
    factory = get_session_factory()
    db = factory()
    # Keep strong references so SQLAlchemy cannot discard stale identity-map objects.
    stale_repeater = db.get(Repeater, DEVICE_A)
    stale_credential = db.get(DeviceCredential, DEVICE_A)
    db.commit()
    with factory() as writer:
        writer.get(DeviceCredential, DEVICE_A).revoked_at = datetime.now(UTC)
        writer.commit()
    topic, envelope = _envelope()
    result = _send(MqttIngestProcessor(lambda: db), topic, envelope)
    assert result.status == "unauthorized_repeater"
    assert stale_credential.revoked_at is not None
    assert stale_repeater.id == DEVICE_A
    _assert_no_analytics()


def test_telemetry_stream_requires_auth(client) -> None:
    response = client.get("/api/telemetry/stream")
    assert response.status_code == 401


def test_telemetry_stream_emits_backlog_event(client) -> None:
    _bootstrap_admin(client)
    token = _login(client)
    broadcaster = get_mqtt_telemetry_broadcaster()
    broadcaster.reset()
    broadcaster.publish(
        {
            "event_id": "evt-1",
            "repeater_id": "rep-1",
            "node_name": "mesh-repeater-01",
            "timestamp": "2026-04-15T12:30:45Z",
            "event_type": "event",
            "event_name": "noise_floor",
            "topic": "glass/mesh-repeater-01/event/noise_floor",
            "payload": {"noise_floor_dbm": -111.2},
            "ingested_at": "2026-04-15T12:30:46Z",
        }
    )

    response = client.get(f"/api/telemetry/stream?token={token}&max_events=1")
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line]
    assert "event: ready" in lines
    payload_lines = [line for line in lines if line.startswith("data: ")]
    assert payload_lines
    payloads = [json.loads(line.removeprefix("data: ")) for line in payload_lines]
    assert any(payload.get("event_id") == "evt-1" for payload in payloads)

    broadcaster.reset()
