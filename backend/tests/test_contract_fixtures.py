"""Source-derived v1 baselines; proposed v2 fixtures are not implemented contracts."""

import copy
import json
from pathlib import Path

import pytest
from app.contracts.v1.inform import (
    CommandResultPayloadV1,
    InformCommandResponseV1,
    InformRequestV1,
)
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "repeater"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name", ["legacy_inform.json", "multi_radio_inform.json", "sensor_edge_inform.json"]
)
def test_source_derived_informs_validate_with_actual_v1_model(name):
    raw = load_fixture(name)
    parsed = InformRequestV1.model_validate(raw)
    assert parsed.pubkey == raw["pubkey"]
    assert parsed.settings == raw["settings"]
    assert parsed.counters.duplicates == 4
    assert parsed.sensors == raw.get("sensors")


def test_null_radio_exposes_existing_producer_consumer_mismatch():
    # The current producer emits zeros with radio_type=None, not fabricated RF values.
    raw = load_fixture("null_radio_inform.json")
    assert raw["settings"]["radio_type"] is None
    assert raw["radio"]["frequency"] == raw["radio"]["bandwidth"] == 0
    with pytest.raises(ValidationError) as caught:
        InformRequestV1.model_validate(raw)
    assert {error["loc"] for error in caught.value.errors()} == {
        ("radio", "frequency"), ("radio", "bandwidth")
    }


def test_multi_radio_baseline_preserves_settings_not_per_radio_telemetry():
    parsed = InformRequestV1.model_validate(load_fixture("multi_radio_inform.json"))
    assert [entry["id"] for entry in parsed.settings["radios"]] == ["rf-a", "rf-b"]
    assert parsed.settings["radios"][1]["radio"]["frequency"] != parsed.radio.frequency
    assert "radios" not in parsed.model_dump()


def test_sensor_unavailability_and_missing_unit_are_not_zero_filled():
    parsed = InformRequestV1.model_validate(load_fixture("sensor_edge_inform.json"))
    assert isinstance(parsed.sensors, dict)
    unavailable, legacy = parsed.sensors["readings"]
    assert unavailable["ok"] is False
    assert unavailable["timestamp"] is None
    assert unavailable["data"] == {}
    assert unavailable["error"] == "RuntimeError: synthetic sensor unavailable"
    assert legacy["data"]["battery_percent"] == 87.5
    assert "unit" not in legacy and "metrics" not in legacy


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("system", "cpu_percent", 101),
        ("system", "memory_percent", -1),
        ("radio", "spreading_factor", 4),
        ("radio", "tx_power", 31),
        ("counters", "rx_total", -1),
        ("counters", "airtime_percent", 101),
    ],
)
def test_fixture_mutations_are_rejected_by_real_model(section, field, value):
    raw = copy.deepcopy(load_fixture("legacy_inform.json"))
    raw[section][field] = value
    with pytest.raises(ValidationError) as caught:
        InformRequestV1.model_validate(raw)
    assert (section, field) in {error["loc"] for error in caught.value.errors()}


def test_unsupported_action_is_valid_command_but_failed_legacy_result():
    command = InformCommandResponseV1.model_validate(load_fixture("unsupported_command.json"))
    result = CommandResultPayloadV1.model_validate(load_fixture("legacy_result.json"))
    assert command.action == "synthetic_unsupported_action"
    assert result.command_id == command.command_id
    assert result.status == "failed"
    assert result.message == f"Unsupported action: {command.action}"
    assert result.details == {}


def test_proposed_v2_inform_is_not_misrepresented_as_v1_support():
    raw = load_fixture("proposed_v2_inform.json")
    with pytest.raises(ValidationError) as caught:
        InformRequestV1.model_validate(raw)
    assert ("version",) in {error["loc"] for error in caught.value.errors()}


def test_proposed_v2_result_status_requires_future_schema():
    raw = load_fixture("proposed_v2_result.json")
    with pytest.raises(ValidationError) as caught:
        CommandResultPayloadV1.model_validate(raw)
    assert ("status",) in {error["loc"] for error in caught.value.errors()}


@pytest.mark.parametrize("status", ["success", "failed", "partial"])
def test_legacy_result_statuses_and_details_survive_validation(status):
    raw = load_fixture("legacy_result.json")
    raw.update(status=status, details={"rule_count": 2, "enabled": True})
    parsed = CommandResultPayloadV1.model_validate(raw)
    assert parsed.status == status
    assert parsed.details == raw["details"]


@pytest.mark.parametrize("field", ["pubkey", "config_hash"])
def test_synthetic_identifiers_still_obey_contract_validation(field):
    raw = load_fixture("legacy_inform.json")
    raw[field] = "not-a-valid-identifier"
    with pytest.raises(ValidationError) as caught:
        InformRequestV1.model_validate(raw)
    assert (field,) in {error["loc"] for error in caught.value.errors()}
