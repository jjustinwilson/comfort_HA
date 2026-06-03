from datetime import datetime, timedelta, timezone
from typing import Any
import asyncio
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, LOG_VERSION


class _VersionedLogger(logging.LoggerAdapter):
    """Logger adapter that prepends the log version to every message."""

    def process(self, msg, kwargs):
        return f"[v{LOG_VERSION}] {msg}", kwargs


_LOGGER = _VersionedLogger(logging.getLogger(__name__))

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
        self.wireless_sensors: dict[str, dict[str, Any]] = {}
        self.device_statuses: dict[str, dict[str, Any]] = {}
        self.zone_notifications: dict[str, dict[str, Any]] = {}

        # Instance variable to store cached commands
        self.cached_commands: dict[tuple[str, str], tuple[str, Any]] = {}

    def _process_pending_commands(self, device_serial: str, device_detail: dict[str, Any]) -> None:
        """Process cached commands and cull outdated commands for a device."""
        self.cull_cached_commands(device_serial)

        # Reapply cached commands to the device details
        for (cached_device_serial, command), (_, command_value) in self.cached_commands.items():
            if cached_device_serial == device_serial:
                device_detail[command] = command_value

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Kumo Cloud.

        Two API endpoints provide device state. Understanding which fields come
        from each is important — they are NOT interchangeable:

        GET /sites/{site_id}/zones  →  zone["adapter"]  (coordinator.zones)
        ─────────────────────────────────────────────────────────────────────
        Adapter-ONLY fields (not present in device detail):
          hasSensor       — whether a wireless room sensor is paired
          hasMhk2         — whether an MHK2 wall controller is paired

        GET /devices/{serial}  →  coordinator.devices[serial]
        ─────────────────────────────────────────────────────────────────────
        Device-detail-ONLY fields (not present in adapter):
          fanSpeed        — current fan speed (e.g. "auto", "quiet", "low"…)
          airDirection    — current vane position (e.g. "horizontal", "auto"…)
          rssi            — WiFi signal strength
          serialNumber    — physical unit serial number
          modelNumber     — model string (e.g. "PVA-A18AA7")
          model           — nested object with brand, family, description, etc.
          displayConfig   — filter/defrost/hotAdjust/standby flags
          unusualFigures, twoFiguresCode, statusDisplay, runTest, etc.

        Fields present in BOTH (device detail is authoritative):
          roomTemp, spCool, spHeat, spAuto
          humidity
          power, operationMode, previousOperationMode
          scheduleOwner, scheduleHoldEndTime
          connected, isHeadless, isSimulator
          lastStatusChangeAt, updatedAt, createdAt, timeZone

        Freshness: device detail's `updatedAt` is consistently more recent than
        adapter's `updatedAt` for the same device, confirming it reflects the
        latest cloud state.  All climate/sensor state properties therefore read
        exclusively from coordinator.devices[serial].  The adapter is used only
        for zone-level metadata (hasSensor, connected as a fallback) and device
        discovery (deviceSerial, zone name).
        """
        try:
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

                    # Build task list — fetch device data, profile, status, and
                    # notification preferences in parallel; add wireless sensor
                    # only for zones that have one (hasSensor flag).
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

                    result_map: dict[str, Any] = {}
                    for key, result in zip(task_keys, results):
                        if isinstance(result, Exception):
                            _LOGGER.debug("Failed to fetch %s for %s: %s", key, device_serial, result)
                            result_map[key] = None
                        else:
                            result_map[key] = result

                    device_detail = result_map.get("detail")
                    if device_detail is None:
                        # GET /devices/{serial} failed (network error, timeout, or non-2xx
                        # response). Rather than overwriting self.devices[serial] with an
                        # empty dict — which would make the entity go unavailable for one
                        # full 60-second poll cycle — fall back to whatever we fetched
                        # successfully last time. Pending commands are still applied below,
                        # so any in-flight optimistic UI updates remain valid.
                        # On the very first poll this will be {} (no previous data), which
                        # correctly leaves the entity unavailable until a fetch succeeds.
                        previous = self.devices.get(device_serial, {})
                        device_detail = dict(previous)  # shallow copy; values are scalars
                        if previous:
                            _LOGGER.warning(
                                "Device details fetch failed for %s; retaining previous data",
                                device_serial,
                            )
                        else:
                            _LOGGER.warning(
                                "Device details fetch failed for %s; no previous data to fall back to",
                                device_serial,
                            )

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

                    _LOGGER.debug(
                        "Device details fetched for %s: roomTemp=%s, spHeat=%s, spCool=%s, "
                        "operationMode=%s, power=%s, fanSpeed=%s, airDirection=%s, humidity=%s",
                        device_serial,
                        device_detail.get("roomTemp"),
                        device_detail.get("spHeat"),
                        device_detail.get("spCool"),
                        device_detail.get("operationMode"),
                        device_detail.get("power"),
                        device_detail.get("fanSpeed"),
                        device_detail.get("airDirection"),
                        device_detail.get("humidity"),
                    )

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

            _LOGGER.debug(
                "Raw cloud data for %s (before pending commands): roomTemp=%s, spHeat=%s, spCool=%s, "
                "operationMode=%s, power=%s, fanSpeed=%s, airDirection=%s, humidity=%s",
                device_serial,
                device_detail.get("roomTemp"),
                device_detail.get("spHeat"),
                device_detail.get("spCool"),
                device_detail.get("operationMode"),
                device_detail.get("power"),
                device_detail.get("fanSpeed"),
                device_detail.get("airDirection"),
                device_detail.get("humidity"),
            )

            # Process pending commands for the device
            self._process_pending_commands(device_serial, device_detail)

            _LOGGER.debug(
                "Device details for %s (after pending commands): roomTemp=%s, spHeat=%s, spCool=%s, "
                "operationMode=%s, power=%s, fanSpeed=%s, airDirection=%s, humidity=%s",
                device_serial,
                device_detail.get("roomTemp"),
                device_detail.get("spHeat"),
                device_detail.get("spCool"),
                device_detail.get("operationMode"),
                device_detail.get("power"),
                device_detail.get("fanSpeed"),
                device_detail.get("airDirection"),
                device_detail.get("humidity"),
            )

            # Update the cached device data
            self.devices[device_serial] = device_detail

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
        """Cache a command with its value and timestamp.

        Cached commands are used for optimistic UI updates: after we send a
        command the Kumo Cloud API may take an indeterminate amount of time to
        reflect the new value in subsequent GET responses.  By storing the
        sent value here and re-applying it over the polled data in
        _process_pending_commands(), the UI immediately shows what the user
        asked for rather than snapping back to the stale cloud value.  Entries
        are evicted by cull_cached_commands() once they exceed the 60-second
        TTL, by which point the cloud should have caught up.

        Also immediately writes the value into coordinator.devices[serial] so
        that async_write_ha_state() called right after cache_commands() picks
        up the user's intent without waiting for the next device refresh.
        """
        current_time = datetime.now(timezone.utc).isoformat()
        self.cached_commands[(device_serial, command)] = (current_time, value)
        _LOGGER.debug(
            "Cached command for device %s: %s=%s (cached_at=%s)",
            device_serial, command, value, current_time
        )
        # Immediately apply to live device data so the first async_write_ha_state()
        # after cache_commands() shows the user's new value rather than stale API data.
        if device_serial in self.devices:
            self.devices[device_serial][command] = value

    def cull_cached_commands(self, device_serial: str) -> None:
        """Remove cached commands for a device that have exceeded the 60-second TTL."""
        to_remove = []
        now = datetime.now(timezone.utc)

        for key, value in self.cached_commands.items():
            cached_device_serial, command = key
            cached_date, _ = value
            cached_date_obj = datetime.fromisoformat(cached_date)
            age = (now - cached_date_obj).total_seconds()

            if cached_device_serial == device_serial and age >= 60:
                _LOGGER.debug(
                    "Evicting cached command %s for device %s (TTL expired, age=%.1fs)",
                    command, cached_device_serial, age,
                )
                to_remove.append(key)

        for key in to_remove:
            del self.cached_commands[key]

        if to_remove:
            _LOGGER.debug(
                "Culled %d TTL-expired cached commands for device %s. Remaining: %d",
                len(to_remove), device_serial, len(self.cached_commands)
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
        self._send_lock = asyncio.Lock()
        self._pending_commands: dict[str, Any] | None = None

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
        """Send a command to the device.

        Enforces two rate-limiting guarantees:

        1. **Serialization** — only one API call is in-flight at a time per
           device.  If a second command arrives while one is already being sent,
           it is held in a last-write-wins pending slot (_pending_commands) and
           dispatched automatically once the in-flight call completes.

        2. **Minimum inter-send gap** — after each send completes (including the
           1-second settle sleep and a device refresh), a further delay is
           inserted so that consecutive sends are always at least 5 seconds
           apart.  This prevents the Kumo Cloud API from receiving rapid-fire
           commands that it may silently drop or apply out of order.
        """
        if self._send_lock.locked():
            if self._pending_commands is None:
                self._pending_commands = {}
            self._pending_commands.update(commands)
            _LOGGER.debug(
                "Command queued for device %s (in-flight): %s", self.device_serial, commands
            )
            return

        async with self._send_lock:
            to_send = commands
            while True:
                sent_at = asyncio.get_event_loop().time()
                try:
                    response = await self.coordinator.api.send_command(self.device_serial, to_send)
                    _LOGGER.debug(
                        "Sent command to device %s: %s, Response: %s",
                        self.device_serial, to_send, response,
                    )

                    # Wait a moment for the command to be processed
                    await asyncio.sleep(1)

                    # Only refresh when no newer command is already queued.
                    # If a pending command exists, its cache entry already
                    # reflects the user's latest intent; refreshing now would
                    # briefly flash the stale intermediate value in the UI.
                    if self._pending_commands is None:
                        await self.coordinator.async_refresh_device(self.device_serial)

                except Exception as err:
                    _LOGGER.error(
                        "Failed to send command to device %s: %s", self.device_serial, err
                    )
                    # Best-effort refresh so the UI reverts to confirmed cloud
                    # state rather than staying on an optimistic cached value.
                    try:
                        await self.coordinator.async_refresh_device(self.device_serial)
                    except Exception:
                        pass
                    raise

                to_send = self._pending_commands
                self._pending_commands = None
                if to_send is None:
                    break

                # Enforce a minimum 5-second gap between sends; the 1s sleep +
                # refresh above already count toward this.
                remaining = 5.0 - (asyncio.get_event_loop().time() - sent_at)
                if remaining > 0:
                    _LOGGER.debug(
                        "Rate limiting device %s: waiting %.1fs before next command",
                        self.device_serial, remaining,
                    )
                    await asyncio.sleep(remaining)

                # Absorb any commands that arrived DURING the rate-limit
                # window (last-write-wins per key).  This prevents a stale
                # intermediate value (e.g. 63°F) from being sent to the cloud
                # after the user has already committed to a newer one (65°F).
                if self._pending_commands is not None:
                    to_send.update(self._pending_commands)
                    self._pending_commands = None
                    _LOGGER.debug(
                        "Merged commands arriving during rate-limit window "
                        "for device %s; will send: %s",
                        self.device_serial, to_send,
                    )

                _LOGGER.debug(
                    "Sending queued command for device %s: %s", self.device_serial, to_send
                )

    def cache_command(self, command: str, value: Any) -> None:
        """Cache a command with its value and timestamp in the coordinator."""
        self.coordinator.cache_command(self.device_serial, command, value)

    def cache_commands(self, commands: dict[str, Any]) -> None:
        """Cache multiple commands with their values and timestamps in the coordinator."""
        for command, value in commands.items():
            self.cache_command(command, value)