"""The Kumo Cloud integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .const import CONF_SITE_ID, DEFAULT_SCAN_INTERVAL, DOMAIN
from .sensor import async_setup_entry as async_setup_sensor_entry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kumo Cloud from a config entry."""

    # Create API client
    api = KumoCloudAPI(hass)

    # Initialize with stored tokens if available
    if "access_token" in entry.data:
        api.username = entry.data[CONF_USERNAME]
        api.access_token = entry.data["access_token"]
        api.refresh_token = entry.data["refresh_token"]

    try:
        # Try to login or refresh tokens
        if not api.access_token:
            await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        else:
            # Verify the token works by making a test request
            try:
                await api.get_account_info()
            except KumoCloudAuthError:
                # Token expired, try to login again
                await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    except KumoCloudAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except KumoCloudConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to connect: {err}") from err

    # Create the coordinator
    coordinator = KumoCloudDataUpdateCoordinator(hass, api, entry.data[CONF_SITE_ID])

    # Fetch initial data so we have data when entities are added
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator in hass data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class KumoCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Kumo Cloud data."""

    def __init__(self, hass: HomeAssistant, api: KumoCloudAPI, site_id: str) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.site_id = site_id
        self.zones: list[dict[str, Any]] = []
        self.devices: dict[str, dict[str, Any]] = {}
        self.device_profiles: dict[str, list[dict[str, Any]]] = {}

        # Instance variable to store cached commands
        # Keys: A tuple of (device_serial: str, command: str).
        # Values: A tuple of (ISO 8601 UTC timestamp: str, command value: Any).
        self.cached_commands: dict[tuple[str, str], tuple[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Kumo Cloud."""
        try:
            # Get zones for the site
            zones = await self.api.get_zones(self.site_id)

            # Get device details for each zone
            devices = {}
            device_profiles = {}

            for zone in zones:
                if "adapter" in zone and zone["adapter"]:
                    device_serial = zone["adapter"]["deviceSerial"]

                    # Get device details and profile in parallel
                    device_detail_task = self.api.get_device_details(device_serial)
                    device_profile_task = self.api.get_device_profile(device_serial)

                    device_detail, device_profile = await asyncio.gather(
                        device_detail_task, device_profile_task
                    )

                    devices[device_serial] = device_detail
                    device_profiles[device_serial] = device_profile

            # Store the data for access by entities
            self.zones = zones
            self.devices = devices
            self.device_profiles = device_profiles

            return {
                "zones": zones,
                "devices": devices,
                "device_profiles": device_profiles,
            }

        except KumoCloudAuthError as err:
            # Try to refresh token once
            try:
                await self.api.refresh_access_token()
                # Retry the request
                return await self._async_update_data()
            except KumoCloudAuthError as refresh_err:
                raise UpdateFailed(
                    f"Authentication failed: {refresh_err}"
                ) from refresh_err
        except KumoCloudConnectionError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_refresh_device(self, device_serial: str) -> None:
        """Refresh a specific device's data immediately."""
        try:
            # Get fresh device details
            device_detail = await self.api.get_device_details(device_serial)

            # Check if the device already exists and the updatedAt matches
            if (
                device_serial in self.devices
                and "updatedAt" in device_detail
            ):
                self.cull_cached_commands(device_serial, device_detail.get("updatedAt"))

            # Reapply cached commands to the device details
            for (cached_device_serial, command), (_, command_value) in self.cached_commands.items():
                if cached_device_serial == device_serial:
                    device_detail[command] = command_value

            # Update the cached device data
            self.devices[device_serial] = device_detail

            # Also update the zone data if it contains the same info
            for zone in self.zones:
                if "adapter" in zone and zone["adapter"]:
                    if zone["adapter"]["deviceSerial"] == device_serial:
                        # Update adapter data with fresh device data
                        zone["adapter"].update(
                            {
                                "roomTemp": device_detail.get("roomTemp"),
                                "operationMode": device_detail.get("operationMode"),
                                "power": device_detail.get("power"),
                                "fanSpeed": device_detail.get("fanSpeed"),
                                "airDirection": device_detail.get("airDirection"),
                                "spCool": device_detail.get("spCool"),
                                "spHeat": device_detail.get("spHeat"),
                                "humidity": device_detail.get("humidity"),
                            }
                        )
                        break

            # Update the coordinator's data dict
            self.data = {
                "zones": self.zones,
                "devices": self.devices,
                "device_profiles": self.device_profiles,
            }

            # Notify all listeners that data has been updated
            self.async_update_listeners()

            _LOGGER.debug("Refreshed device %s data", device_serial)

        except Exception as err:
            _LOGGER.warning("Failed to refresh device %s: %s", device_serial, err)

    def cache_command(self, device_serial: str, command: str, value: Any) -> None:
        """Cache a command with its value and timestamp."""
        current_time = datetime.now(timezone.utc).isoformat()
        self.cached_commands[(device_serial, command)] = (current_time, value)
        _LOGGER.debug("Cached command in device data: %s at %s", command, current_time)

    def cull_cached_commands(self, device_serial: str, date: str) -> None:
        """Remove cached commands for a device where the date is on or after the item's timestamp."""
        to_remove = []
        input_date = datetime.fromisoformat(date)

        for key, value in self.cached_commands.items():
            cached_device_serial, command = key
            cached_date, _ = value
            cached_date_obj = datetime.fromisoformat(cached_date)

            # Check if the device_serial matches and the input date is on or after the cached date
            if cached_device_serial == device_serial and input_date >= cached_date_obj:
                to_remove.append(key)
            else:
                # Log details if the condition fails
                _LOGGER.debug(
                    "Skipping cached command: cached_device_serial=%s, device_serial=%s, "
                    "input_date=%s, cached_date_obj=%s, date=%s, cached_date=%s",
                    cached_device_serial,
                    device_serial,
                    input_date,
                    cached_date_obj,
                    date,
                    cached_date,
                )

        # Remove the matching keys
        for key in to_remove:
            del self.cached_commands[key]

        # Log the culled and remaining commands
        remaining_count = len(self.cached_commands)
        _LOGGER.debug(
            "Culled %d cached commands for device %s on or after %s. Remaining cached commands: %d",
            len(to_remove), device_serial, date, remaining_count
        )

class KumoCloudDevice:
    """Representation of a Kumo Cloud device."""

    def __init__(
        self,
        coordinator: KumoCloudDataUpdateCoordinator,
        zone_id: str,
        device_serial: str,
    ) -> None:
        """Initialize the device."""
        self.coordinator = coordinator
        self.zone_id = zone_id
        self.device_serial = device_serial
        self._zone_data: dict[str, Any] | None = None
        self._device_data: dict[str, Any] | None = None
        self._profile_data: list[dict[str, Any]] | None = None

    @property
    def zone_data(self) -> dict[str, Any]:
        """Get the zone data."""
        # Always get fresh data from coordinator
        for zone in self.coordinator.zones:
            if zone["id"] == self.zone_id:
                return zone
        return {}

    @property
    def device_data(self) -> dict[str, Any]:
        """Get the device data."""
        # Always get fresh data from coordinator
        return self.coordinator.devices.get(self.device_serial, {})

    @property
    def profile_data(self) -> list[dict[str, Any]]:
        """Get the device profile data."""
        # Always get fresh data from coordinator
        return self.coordinator.device_profiles.get(self.device_serial, [])

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        adapter = self.zone_data.get("adapter", {})
        device_data = self.device_data

        # Check both adapter and device data for connection status
        adapter_connected = adapter.get("connected", False)
        device_connected = device_data.get("connected", adapter_connected)

        return device_connected

    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self.zone_data.get("name", f"Zone {self.zone_id}")

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the device."""
        return f"{self.device_serial}_{self.zone_id}"

    async def send_command(self, commands: dict[str, Any]) -> None:
        """Send a command to the device and refresh status."""
        try:
            # Send the command
            await self.coordinator.api.send_command(self.device_serial, commands)
            _LOGGER.debug("Sent command to device %s: %s", self.device_serial, commands)

            # Wait a moment for the command to be processed
            await asyncio.sleep(1)

            # Refresh this specific device's data immediately
            await self.coordinator.async_refresh_device(self.device_serial)

        except Exception as err:
            _LOGGER.error(
                "Failed to send command to device %s: %s", self.device_serial, err
            )
            raise

    def cache_command(self, command: str, value: Any) -> None:
        """Cache a command with its value and timestamp in the coordinator."""
        self.coordinator.cache_command(self.device_serial, command, value)

    def cache_commands(self, commands: dict[str, Any]) -> None:
        """Cache multiple commands with their values and timestamps in the coordinator."""
        for command, value in commands.items():
            self.cache_command(command, value)
