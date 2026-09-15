from datetime import datetime, timedelta, timezone
from typing import Any
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

class KumoCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Kumo Cloud data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: KumoCloudAPI,
        site_id: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.site_id = site_id
        self.config_entry = config_entry
        self.zones: list[dict[str, Any]] = []
        self.devices: dict[str, dict[str, Any]] = {}
        self.device_profiles: dict[str, list[dict[str, Any]]] = {}
        self.wireless_sensors: dict[str, dict[str, Any]] = {}
        self.device_statuses: dict[str, dict[str, Any]] = {}
        self.zone_notifications: dict[str, dict[str, Any]] = {}

        # Instance variable to store cached commands
        self.cached_commands: dict[tuple[str, str], tuple[str, Any]] = {}

    def _process_pending_commands(self, device_serial: str, device_detail: dict[str, Any]) -> None:
        """Process cached commands and cull outdated commands for a device."""
        # Check if the device already exists and the updatedAt matches
        if device_serial in self.devices and "updatedAt" in device_detail:
            self.cull_cached_commands(device_serial, device_detail.get("updatedAt"))

        # Reapply cached commands to the device details
        for (cached_device_serial, command), (_, command_value) in self.cached_commands.items():
            if cached_device_serial == device_serial:
                device_detail[command] = command_value

    def _persist_tokens(self) -> None:
        """Persist rotated access and refresh tokens to the config entry."""
        if self.config_entry is None:
            return

        updated_data = dict(self.config_entry.data)
        needs_entry_update = False

        if self.api.access_token and updated_data.get("access_token") != self.api.access_token:
            updated_data["access_token"] = self.api.access_token
            needs_entry_update = True

        if self.api.refresh_token and updated_data.get("refresh_token") != self.api.refresh_token:
            updated_data["refresh_token"] = self.api.refresh_token
            needs_entry_update = True

        if needs_entry_update:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=updated_data,
            )

    async def _refresh_access_token(self) -> None:
        """Refresh the API token and persist any token rotation."""
        await self.api.refresh_access_token()
        self._persist_tokens()

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch data from Kumo Cloud."""
        # Get zones for the site
        zones = await self.api.get_zones(self.site_id)

        # Get device details for each zone
        devices = {}
        device_profiles = {}
        wireless_sensors = {}
        device_statuses = {}
        zone_notifications = {}

        for zone in zones:
            if "adapter" in zone and zone["adapter"]:
                device_serial = zone["adapter"]["deviceSerial"]
                zone_id = zone["id"]
                has_sensor = zone["adapter"].get("hasSensor", False)

                # Build task list - fetch everything in parallel
                task_keys = ["detail", "profile", "status", "notifications"]
                tasks = [
                    self.api.get_device_details(device_serial),
                    self.api.get_device_profile(device_serial),
                    self.api.get_device_status(device_serial),
                    self.api.get_zone_notification_preferences(zone_id),
                ]
                # Also fetch wireless sensor data if the zone has one
                if has_sensor:
                    task_keys.append("sensor")
                    tasks.append(self.api.get_wireless_sensor(device_serial))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results by key
                result_map = {}
                for key, result in zip(task_keys, results):
                    if isinstance(result, KumoCloudAuthError):
                        raise result
                    if isinstance(result, Exception):
                        _LOGGER.debug("Failed to fetch %s for %s: %s", key, device_serial, result)
                        result_map[key] = None
                    else:
                        result_map[key] = result

                device_detail = result_map.get("detail") or {}

                # Process pending commands for the device
                self._process_pending_commands(device_serial, device_detail)

                devices[device_serial] = device_detail
                device_profiles[device_serial] = result_map.get("profile") or []

                if result_map.get("status"):
                    device_statuses[device_serial] = result_map["status"]

                if result_map.get("notifications"):
                    zone_notifications[zone_id] = result_map["notifications"]

                if has_sensor and result_map.get("sensor"):
                    wireless_sensors[device_serial] = result_map["sensor"]

        # Store the data for access by entities
        self.zones = zones
        self.devices = devices
        self.device_profiles = device_profiles
        self.wireless_sensors = wireless_sensors
        self.device_statuses = device_statuses
        self.zone_notifications = zone_notifications

        return {
            "zones": zones,
            "devices": devices,
            "device_profiles": device_profiles,
            "wireless_sensors": wireless_sensors,
            "device_statuses": device_statuses,
            "zone_notifications": zone_notifications,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Kumo Cloud."""
        try:
            data = await self._fetch_data()
            self._persist_tokens()
            return data

        except KumoCloudAuthError as err:
            try:
                await self._refresh_access_token()
            except KumoCloudAuthError as refresh_err:
                raise ConfigEntryAuthFailed(
                    f"Authentication failed: {refresh_err}"
                ) from refresh_err
            except KumoCloudConnectionError as refresh_err:
                raise UpdateFailed(
                    f"Error during token refresh: {refresh_err}"
                ) from refresh_err
            except Exception as refresh_err:
                raise UpdateFailed(
                    f"Unexpected error during token refresh: {refresh_err}"
                ) from refresh_err

            try:
                data = await self._fetch_data()
                self._persist_tokens()
                return data
            except KumoCloudAuthError as retry_err:
                raise ConfigEntryAuthFailed(
                    f"Authentication failed after token refresh: {retry_err}"
                ) from retry_err
            except KumoCloudConnectionError as retry_err:
                raise UpdateFailed(f"Error communicating with API: {retry_err}") from retry_err
            except Exception as retry_err:
                raise UpdateFailed(f"Unexpected error: {retry_err}") from retry_err
        except KumoCloudConnectionError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_refresh_device(self, device_serial: str) -> None:
        """Refresh a specific device's data immediately."""
        try:
            # Get fresh device details
            device_detail = await self.api.get_device_details(device_serial)
            self._persist_tokens()

            # Process pending commands for the device
            self._process_pending_commands(device_serial, device_detail)

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
                "wireless_sensors": self.wireless_sensors,
                "device_statuses": self.device_statuses,
                "zone_notifications": self.zone_notifications,
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
    def has_wireless_sensor(self) -> bool:
        """Return True if this device has a wireless sensor attached."""
        zone = self.zone_data
        adapter = zone.get("adapter", {})
        return adapter.get("hasSensor", False)

    @property
    def wireless_sensor_data(self) -> dict[str, Any] | None:
        """Get the wireless sensor data (battery, temp, humidity, rssi)."""
        return self.coordinator.wireless_sensors.get(self.device_serial)

    @property
    def device_status_data(self) -> dict[str, Any] | None:
        """Get device status data (firmware, WiFi signal, router info)."""
        return self.coordinator.device_statuses.get(self.device_serial)

    @property
    def zone_notification_data(self) -> dict[str, Any] | None:
        """Get zone notification preferences (filter reminders, alert settings)."""
        return self.coordinator.zone_notifications.get(self.zone_id)

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
            response = await self.coordinator.api.send_command(self.device_serial, commands)
            _LOGGER.debug("Sent command to device %s: %s, Response: %s", self.device_serial, commands, response)

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
