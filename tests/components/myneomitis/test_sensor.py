"""Tests for the MyNeomitis sensor platform."""

from unittest.mock import AsyncMock, Mock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.myneomitis import sensor as sensor_mod
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.common import MockConfigEntry, snapshot_platform

SAMPLE_DEVICE = {
    "_id": "dev1",
    "name": "Device 1",
    "model": "EV30",
    "state": {"consumption": 1500, "connected": True},
    "connected": True,
}

SAMPLE_SUB_DEVICE = {
    "_id": "sub_dev1",
    "name": "Sub Device 1",
    "model": "NTD",
    "state": {"ntc0Temp": 19.3, "connected": True},
    "parents": ",gw-1,",
    "rfid": "rfid-sub-1",
    "connected": True,
}


async def test_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_pyaxenco_client: AsyncMock,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test sensor entities are created for supported devices."""
    sensor_only_device = {
        **SAMPLE_DEVICE,
        "model": "SENSOR_ONLY",
    }
    sensor_only_sub_device = {
        **SAMPLE_SUB_DEVICE,
        "model": "SENSOR_ONLY",
    }

    mock_pyaxenco_client.get_devices.return_value = [
        sensor_only_device,
        sensor_only_sub_device,
        {
            "_id": "unsupported",
            "name": "Unsupported Device",
            "model": "UNKNOWN",
            "state": {},
            "connected": True,
        },
    ]

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_sensor_entity_conventions() -> None:
    """Test sensor metadata follows push-based platform conventions."""
    api = AsyncMock()

    energy_sensor = sensor_mod.DevicesEnergySensor(api, {**SAMPLE_DEVICE}, 0.0)
    assert energy_sensor.should_poll is False
    assert energy_sensor.translation_key == "energy"

    ntc_sensor = sensor_mod.NTCTemperatureSensor(api, {**SAMPLE_SUB_DEVICE}, 0, None)
    assert ntc_sensor.should_poll is False
    assert ntc_sensor.state_class == sensor_mod.SensorStateClass.MEASUREMENT
    assert ntc_sensor.translation_key == "ntc_temperature"
    assert ntc_sensor.translation_placeholders == {"index": "1"}


async def test_async_added_to_hass_register_listener(
    hass: HomeAssistant,
) -> None:
    """Test that async_added_to_hass registers the websocket listener."""
    api = AsyncMock()
    unsub = Mock()
    api.register_listener = Mock(return_value=unsub)
    dev = {**SAMPLE_DEVICE}
    entity = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    entity.hass = hass
    await entity.async_added_to_hass()
    api.register_listener.assert_called_once_with("dev1", entity.handle_ws_update)


async def test_async_added_to_hass_register_listener_none(
    hass: HomeAssistant,
) -> None:
    """Test that async_added_to_hass handles register_listener returning None."""
    api = AsyncMock()
    api.register_listener = Mock(return_value=None)
    dev = {**SAMPLE_DEVICE}
    entity = sensor_mod.DevicesEnergySensor(api, dev, 0.0)
    entity.hass = hass
    await entity.async_added_to_hass()


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
        "parents": ",gw-1,",
        "rfid": "rfid-1",
    }

    sensor = sensor_mod.NTCTemperatureSensor(api, dev, 0, None)
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
        "parents": ",gw-1,",
        "rfid": "rfid-1",
    }

    sensor = sensor_mod.NTCTemperatureSensor(api, dev, 0, None)
    sensor.handle_ws_update({"ntc0Temp": 19.2})
    assert sensor.native_value == 19.2


async def test_helpers_and_edge_cases() -> None:
    """Cover helper functions and edge cases for sensors."""
    assert sensor_mod.get_ntc_indexes({"ntc0Temp": 20, "ntc2Temp": 30}) == [0, 2]

    resp_map = {"a": {"rfid": "r1", "state": {}}, "b": {"rfid": "r2"}}
    assert sensor_mod.get_device_by_rfid(resp_map, "r2")["rfid"] == "r2"

    resp_devices = {"devices": [{"rfid": "rx"}, {"rfid": "ry"}]}
    assert sensor_mod.get_device_by_rfid(resp_devices, "ry")["rfid"] == "ry"

    resp_list = [{"rfid": "rx"}, {"rfid": "ry"}]
    assert sensor_mod.get_device_by_rfid(resp_list, "ry")["rfid"] == "ry"

    api = AsyncMock()
    dev = {**SAMPLE_DEVICE}
    sensor = sensor_mod.DevicesEnergySensor(api, dev, 2.0)
    dev["state"] = {"consumption": 1000}
    assert sensor.native_value == 0.0


async def test_devices_energy_async_update_error_handling(
    hass: HomeAssistant,
) -> None:
    """Test async_update error handling marks entity unavailable."""
    api_err = AsyncMock()
    api_err.get_device_state.side_effect = TimeoutError
    dev = {**SAMPLE_DEVICE}
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
        "parents": ",gw,",
        "rfid": "r1",
    }
    ent = sensor_mod.NTCTemperatureSensor(api, dev, 0, None)
    await ent.async_update()
    assert ent.native_value == 12.3

    ent.handle_ws_update({"connected": False})
    assert ent._attr_available is False


async def test_helpers_none_and_exception_paths() -> None:
    """Test helper edge cases: None inputs and exception in label."""
    assert sensor_mod.get_device_by_rfid(None, "x") is None
    assert sensor_mod.get_device_by_rfid({"a": 1}, None) is None


async def test_native_value_none_and_ntc_low_values() -> None:
    """Test native_value returns None for missing consumption and low temps."""
    api = AsyncMock()
    dev_no_state = {"_id": "d1", "name": "NoState", "model": "EV30", "state": {}}
    sensor = sensor_mod.DevicesEnergySensor(api, dev_no_state, 0.0)
    assert sensor.native_value is None

    dev_low = {
        "_id": "s2",
        "name": "Low",
        "model": "NTD",
        "state": {"ntc0Temp": -60},
        "parents": ",gw-low,",
        "rfid": "r-low",
    }
    ntc = sensor_mod.NTCTemperatureSensor(api, dev_low, 0, None)
    assert ntc.native_value is None


async def test_async_setup_entry_creates_entities_and_updates_options(
    hass: HomeAssistant, mock_pyaxenco_client: AsyncMock
) -> None:
    """Test that async_setup_entry adds sensors and updates options for offsets."""
    device = {
        "_id": "dev_setup",
        "name": "SetupDevice",
        "model": "EV30",
        "state": {"consumption": 1234, "ctnType": 7, "ntc0Temp": 21.0},
        "parents": ",gw-setup,",
        "rfid": "r-setup",
    }
    mock_pyaxenco_client.get_devices.return_value = [device]
    entry = MockConfigEntry(
        domain="myneomitis",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "password"},
        options={},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert len(hass.states.async_entity_ids("sensor")) >= 1
    assert f"{device['_id']}_offset" in entry.options


async def test_async_setup_entry_no_devices(
    hass: HomeAssistant, mock_pyaxenco_client: AsyncMock
) -> None:
    """Test async_setup_entry with no devices."""
    mock_pyaxenco_client.get_devices.return_value = []
    entry = MockConfigEntry(
        domain="myneomitis",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "password"},
        options={},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert len(hass.states.async_entity_ids("sensor")) == 0


@pytest.mark.parametrize(
    ("sensor_class", "device_data", "extra_args"),
    [
        (sensor_mod.DevicesEnergySensor, SAMPLE_DEVICE, (0.0,)),
        (sensor_mod.NTCTemperatureSensor, SAMPLE_SUB_DEVICE, (0, None)),
    ],
)
async def test_websocket_logging(
    sensor_class, device_data, extra_args, caplog: pytest.LogCaptureFixture
) -> None:
    """Test websocket listener logs availability changes only once."""
    api = AsyncMock()
    entity = sensor_class(api, {**device_data}, *extra_args)
    entity.entity_id = "sensor.test"
    entity._attr_available = True

    # Goes offline
    entity.handle_ws_update({"connected": False})
    assert "is unavailable" in caplog.text
    caplog.clear()

    # Stays offline, should not log again
    entity.handle_ws_update({"connected": False})
    assert not caplog.text

    # Comes back online
    entity.handle_ws_update({"connected": True})
    assert "is back online" in caplog.text
    caplog.clear()

    # Stays online, should not log again
    entity.handle_ws_update({"connected": True})
    assert not caplog.text


async def test_ntc_sensor_update_missing_parents_rfid() -> None:
    """Test NTC sensor becomes unavailable if parents or rfid are missing."""
    api = AsyncMock()
    device = {**SAMPLE_SUB_DEVICE, "parents": None, "rfid": None}
    sensor = sensor_mod.NTCTemperatureSensor(api, device, 0, None)
    sensor._attr_available = True

    await sensor.async_update()
    assert sensor.available is False


@pytest.mark.parametrize(
    ("sensor_class", "device_data", "extra_args", "api_method", "api_return"),
    [
        (
            sensor_mod.DevicesEnergySensor,
            SAMPLE_DEVICE,
            (0.0,),
            "get_device_state",
            None,
        ),
        (
            sensor_mod.DevicesEnergySensor,
            SAMPLE_DEVICE,
            (0.0,),
            "get_device_state",
            {"state": None},
        ),
        (
            sensor_mod.NTCTemperatureSensor,
            SAMPLE_SUB_DEVICE,
            (0, None),
            "get_sub_device_state",
            None,
        ),
        (
            sensor_mod.NTCTemperatureSensor,
            SAMPLE_SUB_DEVICE,
            (0, None),
            "get_sub_device_state",
            [{"rfid": "rfid-sub-1", "state": None}],
        ),
    ],
)
async def test_async_update_invalid_response(
    sensor_class,
    device_data,
    extra_args,
    api_method,
    api_return,
) -> None:
    """Test async_update handles invalid API responses gracefully."""
    api = AsyncMock()
    getattr(api, api_method).return_value = api_return
    entity = sensor_class(api, {**device_data}, *extra_args)
    initial_value = entity.native_value

    await entity.async_update()

    assert entity.native_value == initial_value
