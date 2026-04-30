"""Sensor entities for MyNeomitis integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

import aiohttp
from pyaxencoapi import PyAxencoAPI

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MyNeomitisConfigEntry, process_connection_update
from .const import DOMAIN


def get_device_by_rfid(response: Any, rfid: str | None) -> dict[str, Any] | None:
    """Find a sub-device in an API response by its `rfid`.

    The API may return a list or a mapping; handle common shapes.
    """
    if response is None or not rfid:
        return None
    if isinstance(response, dict):
        for val in response.values():
            if isinstance(val, dict) and val.get("rfid") == rfid:
                return val
        devices = response.get("devices") if isinstance(response, dict) else None
        if isinstance(devices, list):
            for dev in devices:
                if dev.get("rfid") == rfid:
                    return dev
        return None
    if isinstance(response, list):
        for dev in response:
            if isinstance(dev, dict) and dev.get("rfid") == rfid:
                return dev
    return None


def get_ntc_indexes(state: Mapping[str, Any]) -> list[int]:
    """Return available NTC probe indexes from device state."""
    return [index for index in range(3) if f"ntc{index}Temp" in state]


@dataclass(frozen=True, kw_only=True)
class MyNeoSensorEntityDescription(SensorEntityDescription):
    """Describe MyNeomitis sensor entity."""

    state_key: str | None = None
    ntc_index: int | None = None


_LOGGER = logging.getLogger(__name__)


class DevicesEnergySensor(SensorEntity):
    """Sensor for tracking MyNeomitis devices energy consumption."""

    entity_description: MyNeoSensorEntityDescription
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        api: PyAxencoAPI,
        device: dict[str, Any],
        base_offset: float,
        description: MyNeoSensorEntityDescription | None = None,
    ) -> None:
        """Initialize the devices energy sensor."""
        self._device_id: str = device["_id"]
        if description is None:
            description = MyNeoSensorEntityDescription(
                key=f"energy_{self._device_id}",
                translation_key="energy",
                state_key="consumption",
            )
        self.entity_description = description
        self._api = api
        self._device = device
        self._attr_unique_id = f"myneo_{self._device_id}_energy"
        self._attr_available = bool(device.get("connected", False))
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("name") or self._device_id,
            manufacturer="Axenco",
            model=device.get("model"),
        )
        self._initial_consumption = base_offset
        self._unavailable_logged: bool = False

    async def async_added_to_hass(self) -> None:
        """Register websocket listener for this sensor."""
        await super().async_added_to_hass()
        if unsubscribe := self._api.register_listener(
            self._device_id, self.handle_ws_update
        ):
            self.async_on_remove(unsubscribe)

    @callback
    def handle_ws_update(self, new_state: dict[str, Any]) -> None:
        """Handle WebSocket updates for the energy sensor."""
        available = process_connection_update(new_state)
        if available is not None:
            self._attr_available = available
            if not available:
                if not self._unavailable_logged:
                    _LOGGER.info("The entity %s is unavailable", self.entity_id)
                    self._unavailable_logged = True
            elif self._unavailable_logged:
                _LOGGER.info("The entity %s is back online", self.entity_id)
                self._unavailable_logged = False

        if not new_state:
            return

        # Update consumption from WebSocket update
        if "consumption" in new_state:
            self._device.setdefault("state", {})["consumption"] = new_state[
                "consumption"
            ]
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the current energy consumption in kWh."""
        consumption = self._device.get("state", {}).get("consumption")
        if consumption is None:
            return None
        current = round(consumption / 1000, 3)
        return max(0.0, round(current - self._initial_consumption, 3))

    async def async_update(self) -> None:
        """Fetch the latest energy consumption from the API."""
        try:
            state = await self._api.get_device_state(self._device["_id"])
        except (
            aiohttp.ClientError,
            aiohttp.ClientResponseError,
            TimeoutError,
            ConnectionError,
        ) as err:
            _LOGGER.debug(
                "Error fetching device state for %s: %s", self._device_id, err
            )
            self._attr_available = False
            return

        if not state or not isinstance(state.get("state"), dict):
            return

        self._device["state"] = state["state"]
        if self.hass is not None:
            self.async_write_ha_state()


class NTCTemperatureSensor(SensorEntity):
    """Sensor for a specific NTC temperature probe on sub-devices."""

    entity_description: MyNeoSensorEntityDescription
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        api: PyAxencoAPI,
        device: dict[str, Any],
        ntc_index: int,
        description: MyNeoSensorEntityDescription | None = None,
    ) -> None:
        """Initialize the NTC temperature sensor."""
        self._device_id: str = device["_id"]
        if description is None:
            description = MyNeoSensorEntityDescription(
                key=f"ntc_{self._device_id}_{ntc_index}",
                translation_key="ntc_temperature",
                translation_placeholders={"index": str(ntc_index + 1)},
                state_key=f"ntc{ntc_index}Temp",
                ntc_index=ntc_index,
                device_class=SensorDeviceClass.TEMPERATURE,
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            )
        self.entity_description = description
        self._api = api
        self._device = device
        self._parents = (
            device.get("parents") if isinstance(device.get("parents"), str) else None
        )
        self._rfid = device.get("rfid") if isinstance(device.get("rfid"), str) else None
        self._ntc_index = ntc_index
        self._attr_unique_id = f"myneo_{self._device_id}_ntc{ntc_index}"
        self._attr_available = bool(device.get("connected", False))
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("name") or self._device_id,
            manufacturer="Axenco",
            model=device.get("model"),
        )
        self._unavailable_logged: bool = False

    async def async_added_to_hass(self) -> None:
        """Register websocket listener for this sensor."""
        await super().async_added_to_hass()
        if unsubscribe := self._api.register_listener(
            self._device_id, self.handle_ws_update
        ):
            self.async_on_remove(unsubscribe)

    @callback
    def handle_ws_update(self, new_state: dict[str, Any]) -> None:
        """Handle WebSocket updates for the NTC temperature sensor."""
        available = process_connection_update(new_state)
        if available is not None:
            self._attr_available = available
            if not available:
                if not self._unavailable_logged:
                    _LOGGER.info("The entity %s is unavailable", self.entity_id)
                    self._unavailable_logged = True
            elif self._unavailable_logged:
                _LOGGER.info("The entity %s is back online", self.entity_id)
                self._unavailable_logged = False

        if not new_state:
            return

        # Update NTC temperature from WebSocket update
        key = f"ntc{self._ntc_index}Temp"
        if key in new_state:
            self._device.setdefault("state", {})[key] = new_state[key]
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the current temperature value or None if invalid."""
        temp = self._device.get("state", {}).get(f"ntc{self._ntc_index}Temp")
        if not isinstance(temp, int | float):
            return None
        return temp if temp > -50 else None

    async def async_update(self) -> None:
        """Fetch the latest NTC temperature value from the sub-device API."""
        if not self._parents or not self._rfid:
            self._attr_available = False
            return

        try:
            response = await self._api.get_sub_device_state(self._parents)
        except (
            aiohttp.ClientError,
            aiohttp.ClientResponseError,
            TimeoutError,
            ConnectionError,
        ) as err:
            _LOGGER.debug(
                "Error fetching sub-device state for %s: %s", self._device_id, err
            )
            self._attr_available = False
            return

        state = get_device_by_rfid(response, self._rfid)
        if not state or not isinstance(state.get("state"), dict):
            return

        self._device["state"] = state["state"]
        if self.hass is not None:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyNeomitisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sensor entities from a config entry."""
    api = config_entry.runtime_data.api
    devices = config_entry.runtime_data.devices

    entities: list[SensorEntity] = []
    options = dict(config_entry.options)
    updated = False

    for device in devices:
        device_id = device.get("_id")
        if not device_id:
            _LOGGER.warning(
                "Skipping sensor device without _id: %s", device.get("name")
            )
            continue

        state = device.get("state")
        if not isinstance(state, dict):
            continue

        # Create energy consumption sensor
        if isinstance(state.get("consumption"), int | float):
            key = f"{device_id}_offset"
            current_value = round(state["consumption"] / 1000, 3)
            base_offset = float(options.get(key, current_value))

            if key not in options:
                options[key] = base_offset
                updated = True

            desc = MyNeoSensorEntityDescription(
                key=f"energy_{device_id}",
                translation_key="energy",
                state_key="consumption",
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                device_class=SensorDeviceClass.ENERGY,
            )
            entities.append(DevicesEnergySensor(api, device, base_offset, desc))

        # Create NTC temperature sensors
        ntc_indexes = get_ntc_indexes(state)
        if ntc_indexes:
            # Skip NTC sensors if sub-device identifiers are missing
            if not isinstance(device.get("parents"), str) or not isinstance(
                device.get("rfid"), str
            ):
                _LOGGER.warning(
                    "Skipping NTC sensors for %s: missing parents or rfid",
                    device_id,
                )
            else:
                for index in ntc_indexes:
                    desc = MyNeoSensorEntityDescription(
                        key=f"ntc_{device_id}_{index}",
                        translation_key="ntc_temperature",
                        translation_placeholders={"index": str(index + 1)},
                        state_key=f"ntc{index}Temp",
                        ntc_index=index,
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                    )
                    entities.append(NTCTemperatureSensor(api, device, index, desc))

    async_add_entities(entities)

    if updated:
        hass.config_entries.async_update_entry(
            config_entry,
            options=options,
        )
