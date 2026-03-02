"""Tests for the MyNeomitis sensor platform."""

from unittest.mock import AsyncMock, Mock

from homeassistant.components.myneomitis import (
    MyNeomitisRuntimeData,
    sensor as sensor_mod,
)
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

SAMPLE_DEVICE = {
    "_id": "dev1",
    "name": "Device 1",
    "model": "EV30",
    "state": {"consumption": 1500, "connected": True},
    "connected": True,
}


async def test_setup_with_discovery_unsubscribe_variants(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_pyaxenco_client: AsyncMock,
) -> None:
    """Test that different unsubscribe return types are handled."""

    mock_pyaxenco_client.get_devices.return_value = [SAMPLE_DEVICE]
    variants = [
        (Mock(return_value=Mock()), "callable"),
        (Mock(return_value=Mock(unsubscribe=Mock())), "unsubscribe_obj"),
        (Mock(return_value=Mock(close=Mock())), "close_obj"),
        (Mock(return_value=None), "none"),
        (Mock(return_value=123), "unsupported"),
    ]

    for reg_mock, name in variants:
        mock_pyaxenco_client.register_listener = reg_mock
        entry = MockConfigEntry(
            domain="myneomitis",
            data=mock_config_entry.data,
            title=f"MyNeomitis ({name})",
            unique_id=f"{mock_config_entry.unique_id}-{name}",
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_devices_energy_sensor_update_direct() -> None:
    """Test DevicesEnergySensor.async_update uses API and updates internal state."""
    api = AsyncMock()
    api.get_device_state = AsyncMock(return_value={"state": {"consumption": 2500}})

    dev = {**SAMPLE_DEVICE}
    sensor = sensor_mod.DevicesEnergySensor(api, dev, 1.5)
    await sensor.async_update()
    assert sensor.native_value == round(2.5 - 1.5, 3)


async def test_handle_ws_update_changes_value() -> None:
    """Test that handle_ws_update updates the sensor state from websocket payload."""
    api = AsyncMock()

    dev = {**SAMPLE_DEVICE}
    sensor = sensor_mod.DevicesEnergySensor(api, dev, 1.0)
    sensor.handle_ws_update({"consumption": 3000})
    assert sensor.native_value == round(3.0 - 1.0, 3)


async def test_ntc_temperature_sensor_update_direct() -> None:
    """Test NTCTemperatureSensor.async_update uses sub-device API and updates state."""
    api = AsyncMock()
    api.get_sub_device_state = AsyncMock(
        return_value=[{"rfid": "rfid-1", "state": {"ntc0Temp": 18.5}}]
    )

    dev = {
        "_id": "sub1",
        "name": "Sub",
        "model": "NTD",
        "state": {"ntc0Temp": 17.0},
        "parents": {"gateway": "gw-1"},
        "rfid": "rfid-1",
    }

    sensor = sensor_mod.NTCTemperatureSensor(api, dev, 0, 0, None)
    await sensor.async_update()
    assert sensor.native_value == 18.5


async def test_ntc_handle_ws_update_changes_value() -> None:
    """Test that NTC handle_ws_update updates the sensor state from websocket payload."""
    api = AsyncMock()

    dev = {
        "_id": "sub1",
        "name": "Sub",
        "model": "NTD",
        "state": {"ntc0Temp": 17.0},
    }

    sensor = sensor_mod.NTCTemperatureSensor(api, dev, 0, 0, None)
    sensor.handle_ws_update({"ntc0Temp": 19.2})
    assert sensor.native_value == 19.2


async def test_helpers_and_edge_cases() -> None:
    """Cover helper functions and edge cases for sensors."""
    assert sensor_mod.CtnType.get_label(5) == "5"

    sensor = sensor_mod.Sensors.from_number(7)
    assert (sensor.ctn0, sensor.ctn1, sensor.ctn2) == (7, 7, 7)

    parents_list = [{"type": "gateway", "id": "gw-1"}, {"type": "other", "id": "o1"}]
    assert sensor_mod.parents_to_dict(parents_list) == {
        "gateway": "gw-1",
        "other": "o1",
    }

    resp_map = {"a": {"rfid": "r1", "state": {}}, "b": {"rfid": "r2"}}
    assert sensor_mod.get_device_by_rfid(resp_map, "r2")["rfid"] == "r2"

    resp_devices = {"devices": [{"rfid": "rx"}, {"rfid": "ry"}]}
    assert sensor_mod.get_device_by_rfid(resp_devices, "ry")["rfid"] == "ry"

    api = AsyncMock()
    dev = {**SAMPLE_DEVICE}
    sensor = sensor_mod.DevicesEnergySensor(api, dev, 2.0)
    dev["state"] = {"consumption": 1000}
    assert sensor.native_value == 0.0


async def test_devices_energy_async_added_and_update_variants(
    hass: HomeAssistant,
) -> None:
    """Test async_added_to_hass handles different register_listener returns and update error handling."""
    api = AsyncMock()
    api.register_listener = Mock(return_value=Mock())
    dev = {**SAMPLE_DEVICE}
    entity = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    entity.hass = hass
    await entity.async_added_to_hass()

    api.register_listener = Mock(return_value=Mock(unsubscribe=Mock()))
    entity2 = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    entity2.hass = hass
    await entity2.async_added_to_hass()

    api.register_listener = Mock(return_value=Mock(close=Mock()))
    entity3 = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    entity3.hass = hass
    await entity3.async_added_to_hass()

    api2 = AsyncMock()
    if hasattr(api2, "register_listener"):
        delattr(api2, "register_listener")
    entity4 = sensor_mod.DevicesEnergySensor(api2, dev, 0.0)
    entity4.hass = hass
    await entity4.async_added_to_hass()

    api_err = AsyncMock()
    api_err.get_device_state.side_effect = TimeoutError
    ent_err = sensor_mod.DevicesEnergySensor(api_err, dev, 0.0)
    await ent_err.async_update()
    assert ent_err._attr_available is False


async def test_ntc_sensor_async_update_and_ws() -> None:
    """Test NTC sensor update error handling and websocket availability."""
    api = AsyncMock()
    api.get_sub_device_state = AsyncMock(
        return_value=[{"rfid": "r1", "state": {"ntc0Temp": 12.3}}]
    )
    dev = {
        "_id": "s1",
        "name": "Sub",
        "model": "NTD",
        "state": {},
        "parents": {"gateway": "gw"},
        "rfid": "r1",
    }
    ent = sensor_mod.NTCTemperatureSensor(api, dev, 0, 0, None)
    await ent.async_update()
    assert ent.native_value == 12.3

    ent.handle_ws_update({"connected": False})
    assert ent._attr_available is False


async def test_helpers_none_and_exception_paths() -> None:
    """Test helper edge cases: None inputs and exception in label."""
    assert sensor_mod.get_device_by_rfid(None, "x") is None
    assert sensor_mod.get_device_by_rfid({"a": 1}, None) is None

    assert sensor_mod.parents_to_dict("not-a-list-or-dict") == {}

    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("bad")

    assert sensor_mod.CtnType.get_label(BadStr()) == ""


async def test_native_value_none_and_ntc_low_values() -> None:
    """Test native_value returns None for missing consumption and low temps."""
    api = AsyncMock()
    dev_no_state = {"_id": "d1", "name": "NoState", "model": "EV30", "state": {}}
    sensor = sensor_mod.DevicesEnergySensor(api, dev_no_state, 0.0)
    assert sensor.native_value is None

    dev_low = {"_id": "s2", "name": "Low", "model": "NTD", "state": {"ntc0Temp": -60}}
    ntc = sensor_mod.NTCTemperatureSensor(api, dev_low, 0, 0, None)
    assert ntc.native_value is None


async def test_async_added_unsupported_and_missing_register_listener(
    hass: HomeAssistant,
) -> None:
    """Ensure async_added_to_hass handles unsupported unsubscribe types and missing register_listener."""
    api = AsyncMock()
    api.register_listener = Mock(return_value=123)
    dev = {**SAMPLE_DEVICE}
    ent = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    ent.hass = hass
    await ent.async_added_to_hass()

    class BareAPI:
        pass

    bare = BareAPI()
    ent2 = sensor_mod.DevicesEnergySensor(bare, dev, 0.0)
    ent2.hass = hass
    await ent2.async_added_to_hass()


async def test_async_setup_entry_creates_entities_and_updates_options(
    hass: HomeAssistant,
) -> None:
    """Test that async_setup_entry adds sensors and updates options for offsets."""
    api = AsyncMock()
    device = {
        "_id": "dev_setup",
        "name": "SetupDevice",
        "model": "EV30",
        "state": {"consumption": 1234, "ctnType": 7, "ntc0Temp": 21.0},
    }

    entry = MockConfigEntry(domain="myneomitis", data={}, options={})
    entry.add_to_hass(hass)
    entry.runtime_data = MyNeomitisRuntimeData(api=api, devices=[device])

    added: list = []

    def add_entities(entities):
        added.extend(entities)

    await sensor_mod.async_setup_entry(hass, entry, add_entities)
    assert len(added) >= 1
    assert f"{device['_id']}_offset" in entry.options
