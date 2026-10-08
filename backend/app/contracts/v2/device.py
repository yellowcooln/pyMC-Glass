"""Enrollment identity metadata and explicit resource/capability inventories."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from app.contracts.v2.common import Contract, PositiveVersion, ResourceID, UUIDValue, uuid_value


class FrozenCapabilities(dict):
    """A serializable map whose ordinary mutation operations fail closed."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("capabilities are immutable; validate a new envelope")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


Capabilities = Annotated[
    dict[ResourceID, PositiveVersion], Field(max_length=128), AfterValidator(FrozenCapabilities)
]


class DeviceIdentityV2(Contract):
    """Enrollment-assigned UUID; metadata is never authentication."""

    device_id: UUIDValue
    node_name: str = Field(min_length=1, max_length=64)
    pubkey: str | None = Field(default=None, pattern=r"^0x[0-9a-f]{64}$")


class RadioV2(Contract):
    id: ResourceID
    enabled: bool
    radio_type: ResourceID | None = None


class IdentityV2(Contract):
    id: ResourceID
    kind: Literal["repeater", "room", "companion"]


class SensorV2(Contract):
    id: ResourceID
    sensor_type: ResourceID
    plugin_id: ResourceID | None = None


class PluginV2(Contract):
    id: ResourceID
    version: str = Field(min_length=1, max_length=64)


class InventoryV2(Contract):
    # Tuples prevent in-place reordering/mutation after validation; JSON remains arrays.
    radios: Annotated[tuple[RadioV2, ...], Field(max_length=32)]
    identities: Annotated[tuple[IdentityV2, ...], Field(max_length=128)]
    sensors: Annotated[tuple[SensorV2, ...], Field(max_length=128)]
    plugins: Annotated[tuple[PluginV2, ...], Field(max_length=128)]

    @model_validator(mode="before")
    @classmethod
    def arrays(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ("radios", "identities", "sensors", "plugins"):
                if type(value.get(key)) is list:
                    value[key] = tuple(value[key])
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        for entries in (self.radios, self.identities, self.sensors, self.plugins):
            if len({entry.id for entry in entries}) != len(entries):
                raise ValueError("resource IDs must be unique within their kind")
        plugin_ids = {entry.id for entry in self.plugins}
        if any(
            sensor.plugin_id is not None and sensor.plugin_id not in plugin_ids
            for sensor in self.sensors
        ):
            raise ValueError("sensor plugin_id must reference an inventoried plugin")
        return self


def negotiate_protocol(*, device_id: str | None, operational_credentials: bool) -> int:
    """Eligibility only, not credential verification or permission to activate transport."""
    if type(operational_credentials) is not bool:
        raise ValueError("operational_credentials must be an explicit boolean")
    if device_id is not None:
        uuid_value(device_id)
    return 2 if device_id is not None and operational_credentials else 1
