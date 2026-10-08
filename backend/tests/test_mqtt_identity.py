"""Pure v2 parser tests, not evidence of activated ingest authorization."""

import json
from datetime import UTC, datetime

import pytest
from app.services.mqtt_identity import parse_managed_message

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
TOPIC = f"glass/device:{A}/packet"


def envelope(**changes):
    value = dict(
        version=2,
        type="packet",
        device_id=A,
        topic=TOPIC,
        node_name="mutable display",
        timestamp="2026-04-15T12:30:45Z",
        payload={},
    )
    value.update(changes)
    return json.dumps(value).encode()


def test_stable_owner_and_display_name():
    result = parse_managed_message(TOPIC, envelope(node_name="another node's name"))
    assert result.device_id == A
    assert result.node_name == "another node's name"
    assert result.timestamp == datetime(2026, 4, 15, 12, 30, 45, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, envelope(device_id=B))
    with pytest.raises(ValueError):
        parse_managed_message(f"glass/device:{B}/packet", envelope())


@pytest.mark.parametrize(
    "suffix",
    [
        "packet",
        "advert",
        "event/noise_floor",
        "event/crc_errors",
        "event/future.extension:1",
        "event/" + "a" * 64,
    ],
)
def test_catalog(suffix):
    topic = f"glass/device:{A}/{suffix}"
    event = suffix.split("/", 1)[1] if suffix.startswith("event/") else None
    changes = dict(topic=topic, type=suffix.split("/")[0])
    if event is not None:
        changes["event_name"] = event
    result = parse_managed_message(topic, envelope(**changes))
    assert result.event_name == event


@pytest.mark.parametrize(
    "topic",
    [
        f"glass/{A}/packet",
        "glass/mutable display/packet",
        f"glass/device:{A.upper()}/packet".replace("0001", "000A"),
        f"glass/device:{A}/event/+",
        f"glass/device:{A}/event/#",
        f"glass/device:{A}/event/a/b",
        f"glass/device:{A}/event/" + "a" * 65,
        f"glass/device:{A}/unknown",
        f"glass/device:{A}/packet/",
        f"other/device:{A}/packet",
    ],
)
def test_invalid_topics(topic):
    with pytest.raises(ValueError):
        parse_managed_message(topic, envelope(topic=topic))


@pytest.mark.parametrize(
    "changes",
    [
        dict(version=1),
        dict(version=True),
        dict(version=2.0),
        dict(device_id=B),
        dict(device_id=A.replace("-", "")),
        dict(topic="elsewhere"),
        dict(type="event"),
        dict(type=[]),
        dict(timestamp=None),
        dict(timestamp=True),
        dict(timestamp="2026-01-01"),
        dict(timestamp="2026-01-01T00:00:00"),
        dict(timestamp=float("inf")),
        dict(timestamp=1e100),
        dict(timestamp=-1),
        dict(payload=[]),
        dict(node_name=None),
        dict(node_name=""),
        dict(extra="ignored?"),
        dict(event_name="packet"),
    ],
)
def test_invalid_envelopes(changes):
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, envelope(**changes))


@pytest.mark.parametrize(
    "field", ["version", "type", "device_id", "topic", "node_name", "timestamp", "payload"]
)
def test_required_fields(field):
    value = json.loads(envelope())
    del value[field]
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, json.dumps(value).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"\xff",
        b"[]",
        b"null",
        None,
        b"{" + b" " * (256 * 1024) + b"}",
        b"[" * 2000 + b"]" * 2000,
        envelope().replace(b'"version": 2', b'"version": 2, "version": 2'),
        envelope(payload={"number": float("nan")}),
        envelope(payload={"number": 1e309}),
        envelope(payload={"text": "\ud800"}),
        envelope(payload={"items": [0] * 4096}),
    ],
)
def test_malformed_bounded_and_unavailable(raw):
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, raw)


def test_depth_and_canonical_payload():
    payload = {}
    for _ in range(17):
        payload = {"nested": payload}
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, envelope(payload=payload))
    result = parse_managed_message(TOPIC, envelope(payload={"z": 0, "a": [1, True]}))
    assert result.canonical_payload == '{"a":[1,true],"z":0}'


def test_exact_size_depth_and_node_boundaries():
    raw = envelope()
    raw += b" " * (256 * 1024 - len(raw))
    assert parse_managed_message(TOPIC, raw).device_id == A
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, raw + b" ")

    # Envelope has 15 nodes; payload member key/list add two more.
    assert parse_managed_message(TOPIC, envelope(payload={"x": [0] * 4079})).device_id == A
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, envelope(payload={"x": [0] * 4080}))

    payload = 0
    for _ in range(13):
        payload = [payload]
    assert parse_managed_message(TOPIC, envelope(payload={"x": payload})).device_id == A
    with pytest.raises(ValueError):
        parse_managed_message(TOPIC, envelope(payload={"x": [payload]}))


def test_event_name_must_match():
    topic = f"glass/device:{A}/event/noise_floor"
    for event_name in (None, "crc_errors", "noise_floor/+", []):
        with pytest.raises(ValueError):
            parse_managed_message(topic, envelope(topic=topic, type="event", event_name=event_name))
