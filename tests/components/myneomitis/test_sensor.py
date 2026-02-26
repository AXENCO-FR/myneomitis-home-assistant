"""Tests for the MyNeomitis sensor platform."""

from unittest.mock import AsyncMock, Mock

import pytest

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
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_pyaxenco_client: AsyncMock
) -> None:
    """Test that different unsubscribe return types are handled."""

    mock_pyaxenco_client.get_devices.return_value = [SAMPLE_DEVICE]

    # Callable unsubscribe returned from register_listener
    mock_pyaxenco_client.register_listener = Mock(return_value=lambda: None)

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Object with unsubscribe
    mock_pyaxenco_client.register_listener = Mock(return_value=Mock(unsubscribe=lambda: None))
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Object with close
    mock_pyaxenco_client.register_listener = Mock(return_value=Mock(close=lambda: None))
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # None
    mock_pyaxenco_client.register_listener = Mock(return_value=None)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Unsupported type
    mock_pyaxenco_client.register_listener = Mock(return_value=123)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_devices_energy_sensor_update_direct() -> None:
    """Test DevicesEnergySensor.async_update uses API and updates internal state."""
    api = AsyncMock()
    api.get_device_state = AsyncMock(return_value={"state": {"consumption": 2500}})
    from homeassistant.components.myneomitis.sensor import DevicesEnergySensor

    dev = {**SAMPLE_DEVICE}
    sensor = DevicesEnergySensor(api, dev, 1.5)
    await sensor.async_update()
    assert sensor.native_value == round(2.5 - 1.5, 3)


async def test_handle_ws_update_changes_value() -> None:
    """Test that handle_ws_update updates the sensor state from websocket payload."""
    api = AsyncMock()
    from homeassistant.components.myneomitis.sensor import DevicesEnergySensor

    dev = {**SAMPLE_DEVICE}
    sensor = DevicesEnergySensor(api, dev, 1.0)
    # simulate websocket update
    sensor.handle_ws_update({"consumption": 3000})
    assert sensor.native_value == round(3.0 - 1.0, 3)
