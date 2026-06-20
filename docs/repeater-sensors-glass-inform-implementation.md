# Repeater implementation handoff: Sensor/UPS telemetry for Glass

## Goal

Make pyMC Repeater dev send sensor and UPS readings to pyMC Glass as soon as the Repeater sensor subsystem has readings.
Glass is already pre-staged to accept a top-level `/inform` field named `sensors` and expose it on the repeater detail API/UI.

## Current Glass contract

Glass `/inform` accepts this optional top-level field:

```json
{
  "type": "inform",
  "version": 1,
  "node_name": "repeater-1",
  "system": { "cpu_percent": 10, "memory_percent": 20, "disk_percent": 30 },
  "radio": { "frequency": 869618000, "spreading_factor": 8, "bandwidth": 62500, "tx_power": 14 },
  "counters": { "rx_total": 0, "tx_total": 0, "forwarded": 0, "dropped": 0, "duplicates": 0, "airtime_percent": 0 },
  "sensors": {
    "enabled": true,
    "poll_interval_seconds": 30,
    "configured": 2,
    "loaded": 2,
    "running": true,
    "readings": [
      {
        "name": "ups-main",
        "type": "waveshare_ups_d",
        "ok": true,
        "timestamp": "2026-06-20T12:00:00Z",
        "data": {
          "battery_percent": 87.5,
          "voltage_v": 4.08,
          "current_ma": 120.0,
          "power_w": 0.49
        }
      }
    ]
  }
}
```

Glass stores this under the repeater's latest `system` detail as `system.sensors`.
The frontend displays `system.sensors.readings[*].data` as Sensor / UPS readings.

## Required Repeater changes

In `repeater/data_acquisition/glass_handler.py`, update `_build_inform_payload()`.

Suggested implementation:

```python
sensors_summary = None
sensor_manager = getattr(self.daemon_instance, "sensor_manager", None)
if sensor_manager is not None:
    try:
        sensors_summary = sensor_manager.get_summary()
    except Exception as exc:
        logger.debug("Failed collecting sensor summary for Glass inform: %s", exc)
        sensors_summary = {
            "enabled": False,
            "configured": 0,
            "loaded": 0,
            "running": False,
            "readings": [],
            "error": str(exc),
        }

payload = {
    ...existing inform fields...,
    "sensors": sensors_summary,
}
```

Only include `sensors` when the field is not `None` if you want smaller payloads:

```python
if sensors_summary is not None:
    payload["sensors"] = sensors_summary
```

## Preferred payload shape

Use the existing Repeater `SensorManager.get_summary()` shape directly:

```json
{
  "enabled": true,
  "poll_interval_seconds": 30,
  "configured": 2,
  "loaded": 2,
  "running": true,
  "readings": [
    {
      "name": "sensor-name",
      "type": "sensor-type",
      "ok": true,
      "timestamp": "RFC3339 timestamp",
      "data": {}
    }
  ]
}
```

Keep values JSON-safe:
- numbers as JSON numbers
- booleans as booleans
- strings as strings
- no bytes objects
- no secrets

## UPS field naming recommendations

For UPS/battery sensors, prefer consistent data keys so Glass can later add first-class gauges:

- `battery_percent`
- `voltage_v`
- `current_ma`
- `power_w`
- `charging` boolean
- `battery_voltage_v`
- `bus_voltage_v`
- `shunt_voltage_mv`
- `temperature_c` if available

Existing sensor-specific fields are still accepted; Glass renders unknown fields generically.

## MQTT event telemetry option

Glass already stores arbitrary MQTT event telemetry in `mqtt_ingest_events`.
If Repeater also wants higher-rate sensor telemetry over MQTT, publish an event envelope via the existing Glass MQTT publisher:

```python
glass_handler.publish_telemetry("sensors", sensor_manager.get_summary())
```

That should land on a topic like:

```text
glass/<node_name>/event/sensors
```

Payload should use the same `SensorManager.get_summary()` shape.

Recommended approach:
- `/inform` carries the latest sensor summary for inventory/detail pages.
- MQTT `event/sensors` can carry higher-frequency readings later.

## Tests to add in Repeater

1. `GlassHandler._build_inform_payload()` includes `sensors` when `daemon_instance.sensor_manager` exists.
2. If `sensor_manager.get_summary()` raises, inform still succeeds and includes a useful sensor error summary or omits `sensors`.
3. A Waveshare/Lafvin/SHTC3 sensor reading remains JSON serializable.
4. Optional: MQTT publishing of `record_type="sensors"` produces `type="event"` and `event_name="sensors"`.

## Acceptance check

1. Enable at least one Repeater sensor in `config.yaml`.
2. Confirm Repeater `/api/stats` includes `sensors` from `daemon.get_stats()`.
3. Confirm Repeater `/inform` payload includes top-level `sensors`.
4. In Glass, open the repeater detail page.
5. The “Sensor / UPS Readings” section should show each reading and its data values.
