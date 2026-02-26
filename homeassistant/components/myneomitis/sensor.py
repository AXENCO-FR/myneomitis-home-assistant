"""Sensor platform for MyNeomitis integration."""

from __future__ import annotations

import logging
from typing import Any
import aiohttp

from pyaxencoapi import PyAxencoAPI

from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MyNeomitisConfigEntry
from .const import DOMAIN

class CtnType:
    """Simple replacement for CTN type labeling."""

    @staticmethod
    def get_label(ctn_type: int) -> str:
        """Return a short label for a CTN type."""
        try:
            return str(ctn_type)
        except Exception:
            return ""


@dataclass
class Sensors:
    """Simple representation of CTN sensor slots."""

    ctn0: int | None = None
    ctn1: int | None = None
    ctn2: int | None = None

    @staticmethod
    def from_number(value: int) -> "Sensors":
        """Construct Sensors from a numeric code.

        This implementation is permissive: it returns the value in each
        slot to preserve ordering used by the platform code.
        """
        return Sensors(ctn0=value, ctn1=value, ctn2=value)


def get_device_by_rfid(response: Any, rfid: str) -> dict | None:
    """Find a sub-device in an API response by its `rfid`.

    The API may return a list or a mapping; handle common shapes.
    """
    if response is None or rfid is None:
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


def parents_to_dict(parents: Any) -> dict:
    """Normalize `parents` to a dictionary mapping.

    Accept both dict and list shapes for backward compatibility.
    """
    if not parents:
        return {}
    if isinstance(parents, dict):
        return parents
    if isinstance(parents, list):
        out: dict[str, Any] = {}
        for item in parents:
            if isinstance(item, dict):
                key = item.get("type") or item.get("key") or item.get("role")
                val = item.get("id") or item.get("value")
                if key and val:
                    out[key] = val
        return out
    return {}

@dataclass
class MyNeoSensorEntityDescription(SensorEntityDescription):
    """Describe MyNeomitis sensor entity."""

    state_key: str | None = None
    ntc_index: int | None = None

from . import process_connection_update

_LOGGER = logging.getLogger(__name__)


class DevicesEnergySensor(SensorEntity):
    """Sensor for tracking MyNeomitis devices energy consumption."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"

    entity_description: MyNeoSensorEntityDescription

    def __init__(
        self,
        api: PyAxencoAPI,
        device: dict[str, Any],
        base_offset: float,
        description: MyNeoSensorEntityDescription | None = None,
    ) -> None:
        """Initialize the devices energy sensor."""
        # If no description provided (tests may construct directly), build a
        # minimal description to keep attributes consistent
        if description is None:
            description = MyNeoSensorEntityDescription(
                key=f"energy_{device.get('_id')}", state_key="consumption"
            )
        self.entity_description = description
        self._api = api
        self._device = device
        self._attr_name = f"MyNeo {device['name']} Energy"
        self._attr_unique_id = f"myneo_{device['_id']}_energy"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, device["_id"])},
            name=device.get("name") or device["_id"],
            manufacturer="Axenco",
            model=device.get("model", ""),
        )
        self._attr_should_poll = True
        self._initial_consumption = base_offset
        self._unavailable_logged: bool = False
        self._device_id = device.get("_id")
        self._attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        """Register websocket listener for this sensor."""
        await super().async_added_to_hass()
        register_listener = getattr(self._api, "register_listener", None)
        if not callable(register_listener):
            _LOGGER.debug(
                "API has no callable register_listener, skipping ws listener for %s",
                self._device_id,
            )
            return

        unsubscribe = register_listener(self._device_id, self.handle_ws_update)

        if callable(unsubscribe):
            self.async_on_remove(unsubscribe)
        elif hasattr(unsubscribe, "unsubscribe"):
            self.async_on_remove(unsubscribe.unsubscribe)
        elif hasattr(unsubscribe, "close"):
            self.async_on_remove(unsubscribe.close)
        elif unsubscribe is None:
            pass
        else:
            _LOGGER.debug(
                "register_listener returned unsupported type %s for %s",
                type(unsubscribe),
                self._device_id,
            )

    def handle_ws_update(self, new_state: dict[str, Any]) -> None:
        """Handle websocket updates for the energy sensor."""
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

        if "consumption" in new_state:
            self._device.setdefault("state", {})["consumption"] = new_state["consumption"]
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
        """Fetch the latest state of devices from the API."""
        try:
            state = await self._api.get_device_state(self._device["_id"])
        except (
            aiohttp.ClientResponseError,
            TimeoutError,
            ConnectionError,
        ) as err:
            _LOGGER.debug(
                "Error fetching device state for %s: %s", self._device_id, err
            )
            self._attr_available = False
            return

        if not state:
            return

        self._device["state"] = state["state"]
        self.async_write_ha_state()


class NTCTemperatureSensor(SensorEntity):
    """Sensor for a specific NTC temperature probe."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    entity_description: MyNeoSensorEntityDescription

    def __init__(
        self,
        api: PyAxencoAPI,
        device: dict[str, Any],
        ntc_index: int,
        ctn_type: int,
        description: MyNeoSensorEntityDescription,
    ) -> None:
        """Initialize the NTC temperature sensor."""
        self.entity_description = description
        self._api = api
        self._device = device
        self._parents = (
            parents_to_dict(device["parents"]) if "parents" in device else {}
        )
        self._ntc_index = ntc_index
        self._ctn_type = ctn_type
        self._attr_name = (
            f"MyNeo {device['name']} {CtnType.get_label(ctn_type)} Temp {ntc_index}"
        )
        self._attr_unique_id = f"myneo_{device['_id']}_ntc{ntc_index}"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, device["_id"])},
            name=device.get("name") or device["_id"],
            manufacturer="Axenco",
            model=device.get("model", ""),
        )
        self._attr_should_poll = True
        self._unavailable_logged: bool = False
        self._device_id = device.get("_id")
        self._attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        """Register websocket listener for this sensor."""
        await super().async_added_to_hass()
        register_listener = getattr(self._api, "register_listener", None)
        if not callable(register_listener):
            _LOGGER.debug(
                "API has no callable register_listener, skipping ws listener for %s",
                self._device_id,
            )
            return

        unsubscribe = register_listener(self._device_id, self.handle_ws_update)

        if callable(unsubscribe):
            self.async_on_remove(unsubscribe)
        elif hasattr(unsubscribe, "unsubscribe"):
            self.async_on_remove(unsubscribe.unsubscribe)
        elif hasattr(unsubscribe, "close"):
            self.async_on_remove(unsubscribe.close)
        elif unsubscribe is None:
            pass
        else:
            _LOGGER.debug(
                "register_listener returned unsupported type %s for %s",
                type(unsubscribe),
                self._device_id,
            )

    def handle_ws_update(self, new_state: dict[str, Any]) -> None:
        """Handle websocket updates for the NTC sensor."""
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

        key = f"ntc{self._ntc_index}Temp"
        if key in new_state:
            self._device.setdefault("state", {})[key] = new_state[key]
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        """Return the current temperature value or None if invalid."""
        temp = self._device.get("state", {}).get(f"ntc{self._ntc_index}Temp")
        if temp is None:
            return None
        return temp if temp > -50 else None

    async def async_update(self) -> None:
        """Fetch the latest state of the NTC temperature sensor."""
        try:
            response = await self._api.get_sub_device_state(self._parents["gateway"])
        except (
            aiohttp.ClientResponseError,
            TimeoutError,
            ConnectionError,
        ) as err:
            _LOGGER.debug(
                "Error fetching sub-device state for %s: %s", self._device_id, err
            )
            self._attr_available = False
            return

        state = get_device_by_rfid(response, self._device["rfid"])
        if not state:
            return
        self._device["state"] = state["state"]
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyNeomitisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sensors from a config entry."""
    api = config_entry.runtime_data.api
    devices = config_entry.runtime_data.devices

    options = dict(config_entry.options)
    updated = False

    added_ids = set()
    entities_by_id: dict[str, list[SensorEntity]] = {}

    def _create_entities(device: dict) -> list[SensorEntity]:
        nonlocal updated
        entities: list[SensorEntity] = []
        state = device.get("state", {})
        uid = device["_id"]
        added_ids.add(uid)

        # Energy sensor
        if "consumption" in state:
            key = f"{uid}_offset"
            current_value = round(state["consumption"] / 1000, 3)
            base_offset = float(options.get(key, current_value))

            if key not in options:
                options[key] = base_offset
                updated = True

            desc = MyNeoSensorEntityDescription(
                key=f"energy_{uid}",
                state_key="consumption",
                native_unit_of_measurement="kWh",
                device_class=SensorDeviceClass.ENERGY,
            )
            entities.append(DevicesEnergySensor(api, device, base_offset, desc))

        # NTC sensors
        if "ctnType" in state:
            ctn_sensors = Sensors.from_number(state["ctnType"])
            for index, ctn_type in enumerate(
                [ctn_sensors.ctn0, ctn_sensors.ctn1, ctn_sensors.ctn2]
            ):
                temp_key = f"ntc{index}Temp"
                if temp_key in state:
                    desc = MyNeoSensorEntityDescription(
                        key=f"ntc_{uid}_{index}",
                        state_key=temp_key,
                        ntc_index=index,
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                    )
                    entities.append(
                        NTCTemperatureSensor(api, device, index, ctn_type, desc)
                    )

        if entities:
            entities_by_id[f"myneo_{uid}"] = entities
        return entities

    initial_entities = []
    for device in devices:
        initial_entities.extend(_create_entities(device))

    async_add_entities(initial_entities)

    if updated:
        hass.config_entries.async_update_entry(
            config_entry,
            options=options,
        )

    async def add_new_entity(device: dict) -> None:
        if device["_id"] in added_ids:
            return
        new_entities = _create_entities(device)
        if new_entities:
            _LOGGER.info(
                "Adding new sensor entity(ies) for %s", device.get("name")
            )
            async_add_entities(new_entities)

    async def remove_entity(device_id: str) -> None:
        uid = f"myneo_{device_id}"
        entities = entities_by_id.get(uid)
        if entities:
            _LOGGER.info("Removing sensor entity(ies): %s", uid)
            for entity in entities:
                await entity.async_remove()
            added_ids.discard(device_id)
            entities_by_id.pop(uid, None)
