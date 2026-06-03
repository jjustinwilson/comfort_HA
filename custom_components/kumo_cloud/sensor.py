"""Platform for Kumo Cloud sensors.

Provides standalone sensor entities for each Mitsubishi zone:
- Temperature (from indoor unit's built-in thermistor)
- Humidity (from indoor unit)
- WiFi Adapter Firmware Version (diagnostic, from /status)
- WiFi Signal Strength (diagnostic, routerRssi from /status)
- Filter Reminder (diagnostic, from /notification-preferences)

For zones with a wireless sensor (PAC-USWHS003-TH-1) attached:
- Wireless Sensor Battery (%)
- Wireless Sensor Signal Strength (RSSI dBm)
- Wireless Sensor Temperature (from the remote sensor itself)
- Wireless Sensor Humidity (from the remote sensor itself)

API endpoints discovered via Proxyman traffic capture of the Comfort app:
- /v3/devices/{serial}/sensor  (wireless sensor data)
- /v3/devices/{serial}/status  (firmware, WiFi signal, router info)
- /v3/zones/{zoneId}/notification-preferences  (filter reminders)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import KumoCloudDataUpdateCoordinator, KumoCloudDevice, _VersionedLogger
from .const import DOMAIN

_LOGGER = _VersionedLogger(logging.getLogger(__name__))

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Kumo Cloud sensor devices."""
    coordinator: KumoCloudDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone in coordinator.zones:
        if "adapter" in zone and zone["adapter"]:
            device_serial = zone["adapter"]["deviceSerial"]
            zone_id = zone["id"]
            has_sensor = zone["adapter"].get("hasSensor", False)

            device = KumoCloudDevice(coordinator, zone_id, device_serial)

            # Indoor unit sensors (always available)
            entities.append(KumoCloudTemperatureSensor(coordinator, device))
            entities.append(KumoCloudHumiditySensor(coordinator, device))

            # Diagnostic sensors from /status endpoint (always available)
            entities.append(KumoCloudFirmwareSensor(coordinator, device))
            entities.append(KumoCloudWiFiSignalSensor(coordinator, device))
            entities.append(KumoCloudFilterReminderSensor(coordinator, device))

            # Wireless sensor entities (only if hasSensor is true)
            if has_sensor:
                entities.append(KumoCloudWirelessBatterySensor(coordinator, device))
                entities.append(KumoCloudWirelessSignalSensor(coordinator, device))
                entities.append(KumoCloudWirelessTemperatureSensor(coordinator, device))
                entities.append(KumoCloudWirelessHumiditySensor(coordinator, device))

    async_add_entities(entities)


def _device_info(device: KumoCloudDevice) -> DeviceInfo:
    """Return standard device info for all sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, device.device_serial)},
        name=device.zone_data.get("name", "Kumo Cloud Device"),
        manufacturer="Mitsubishi Electric",
    )


# =============================================================================
# Indoor unit sensors
# =============================================================================

class KumoCloudTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Temperature from the indoor unit's built-in thermistor."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        """Initialize the temperature sensor."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Temperature"
        self._attr_unique_id = f"{device.device_serial}_temperature"
        # NOTE: native_unit_of_measurement = CELSIUS means HA uses standard math
        # (temp * 9/5 + 32) when displaying in Fahrenheit. This diverges from
        # Mitsubishi's proprietary F<->C lookup tables used by the climate entity
        # (see _c_to_f / _C_TO_F in climate.py). At certain values the two
        # entities will show different readings for the same underlying roomTemp:
        #   19.0 °C → sensor 66 °F (std) vs climate 67 °F (table)
        #   21.0 °C → sensor 70 °F (std) vs climate 69 °F (table)
        #   22.0 °C → sensor 72 °F (std) vs climate 71 °F (table)
        # Fixing this requires dropping native_unit_of_measurement and managing
        # the unit dynamically (matching hass.config.units.temperature_unit), which
        # also means sharing the lookup tables with this module — consider extracting
        # them to a shared temp_util.py. Deferred: impact is cosmetic/display-only.
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        return self.device.device_data.get("roomTemp")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self.device)


class KumoCloudHumiditySensor(CoordinatorEntity, SensorEntity):
    """Humidity from the indoor unit."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        """Initialize the humidity sensor."""
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Humidity"
        self._attr_unique_id = f"{device.device_serial}_humidity"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the current humidity."""
        return self.device.device_data.get("humidity")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self.device)


# =============================================================================
# Diagnostic sensors from /devices/{serial}/status
# =============================================================================

class KumoCloudFirmwareSensor(CoordinatorEntity, SensorEntity):
    """WiFi adapter firmware version."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Firmware"
        self._attr_unique_id = f"{device.device_serial}_firmware"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:chip"

    @property
    def native_value(self) -> str | None:
        status = self.device.device_status_data
        if status is None:
            return None
        return status.get("firmwareVersion")

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


class KumoCloudWiFiSignalSensor(CoordinatorEntity, SensorEntity):
    """WiFi adapter signal strength to the router."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} WiFi Signal"
        self._attr_unique_id = f"{device.device_serial}_wifi_rssi"
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        status = self.device.device_status_data
        if status is None:
            return None
        return status.get("routerRssi")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Include router SSID as an attribute."""
        status = self.device.device_status_data
        if status and status.get("routerSsid"):
            return {"router_ssid": status["routerSsid"]}
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


class KumoCloudFilterReminderSensor(CoordinatorEntity, SensorEntity):
    """Last filter dirty reminder date."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Filter Reminder"
        self._attr_unique_id = f"{device.device_serial}_filter_reminder"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:air-filter"

    @property
    def native_value(self) -> datetime | None:
        notifications = self.device.zone_notification_data
        if notifications is None:
            return None
        last_sent = notifications.get("filterDirtyReminderLastSent")
        if last_sent:
            try:
                return datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Include reminder interval as an attribute."""
        notifications = self.device.zone_notification_data
        if notifications:
            attrs: dict[str, Any] = {}
            interval = notifications.get("filterDirtyReminderInterval")
            if interval is not None:
                attrs["reminder_interval_days"] = interval
            enabled = notifications.get("filterDirty")
            if enabled is not None:
                attrs["reminders_enabled"] = enabled
            return attrs
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


# =============================================================================
# Wireless sensor entities (PAC-USWHS003-TH-1)
# =============================================================================

class KumoCloudWirelessBatterySensor(CoordinatorEntity, SensorEntity):
    """Battery level of the wireless temperature/humidity sensor."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Wireless Sensor Battery"
        self._attr_unique_id = f"{device.device_serial}_wireless_battery"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        sensor_data = self.device.wireless_sensor_data
        if sensor_data is None:
            return None
        return sensor_data.get("battery")

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


class KumoCloudWirelessSignalSensor(CoordinatorEntity, SensorEntity):
    """Signal strength (RSSI) of the wireless sensor to the WiFi adapter."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Wireless Sensor Signal"
        self._attr_unique_id = f"{device.device_serial}_wireless_rssi"
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        sensor_data = self.device.wireless_sensor_data
        if sensor_data is None:
            return None
        return sensor_data.get("rssi")

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


class KumoCloudWirelessTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Temperature reading from the wireless sensor itself."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Wireless Sensor Temperature"
        self._attr_unique_id = f"{device.device_serial}_wireless_temperature"
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        sensor_data = self.device.wireless_sensor_data
        if sensor_data is None:
            return None
        temp = sensor_data.get("temperature")
        if temp is not None:
            return round(temp, 1)
        return None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)


class KumoCloudWirelessHumiditySensor(CoordinatorEntity, SensorEntity):
    """Humidity reading from the wireless sensor itself."""

    def __init__(self, coordinator: KumoCloudDataUpdateCoordinator, device: KumoCloudDevice) -> None:
        super().__init__(coordinator)
        self.device = device
        self._attr_name = f"{device.zone_data.get('name', 'Kumo Cloud')} Wireless Sensor Humidity"
        self._attr_unique_id = f"{device.device_serial}_wireless_humidity"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        sensor_data = self.device.wireless_sensor_data
        if sensor_data is None:
            return None
        humidity = sensor_data.get("humidity")
        if humidity is not None:
            return round(humidity, 1)
        return None

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self.device)