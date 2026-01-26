"""Platform for Kumo Cloud climate integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import KumoCloudDataUpdateCoordinator, KumoCloudDevice
from .const import (
    DOMAIN,
    OPERATION_MODE_OFF,
    OPERATION_MODE_COOL,
    OPERATION_MODE_HEAT,
    OPERATION_MODE_DRY,
    OPERATION_MODE_VENT,
    OPERATION_MODE_AUTO,
    OPERATION_MODE_AUTO_COOL,
    OPERATION_MODE_AUTO_HEAT,
    API_TO_UI_FAN,
    UI_TO_API_FAN,
    UI_FAN_SPEEDS,
    API_TO_UI_VANE,
    UI_TO_API_VANE,
    UI_VANE_POSITIONS,
)

_LOGGER = logging.getLogger(__name__)

# Mapping from Kumo Cloud operation modes to Home Assistant HVAC modes
KUMO_TO_HVAC_MODE = {
    OPERATION_MODE_OFF: HVACMode.OFF,
    OPERATION_MODE_COOL: HVACMode.COOL,
    OPERATION_MODE_HEAT: HVACMode.HEAT,
    OPERATION_MODE_DRY: HVACMode.DRY,
    OPERATION_MODE_VENT: HVACMode.FAN_ONLY,
    OPERATION_MODE_AUTO: HVACMode.HEAT_COOL,
    OPERATION_MODE_AUTO_COOL: HVACMode.HEAT_COOL,
    OPERATION_MODE_AUTO_HEAT: HVACMode.HEAT_COOL,
}

# Reverse mapping
HVAC_TO_KUMO_MODE = {v: k for k, v in KUMO_TO_HVAC_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Kumo Cloud climate devices."""
    coordinator: KumoCloudDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone in coordinator.zones:
        if "adapter" in zone and zone["adapter"]:
            device_serial = zone["adapter"]["deviceSerial"]
            zone_id = zone["id"]

            device = KumoCloudDevice(coordinator, zone_id, device_serial)
            entities.append(KumoCloudClimate(device))

    async_add_entities(entities)


class KumoCloudClimate(CoordinatorEntity, ClimateEntity):
    """Representation of a Kumo Cloud climate device."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, device: KumoCloudDevice) -> None:
        """Initialize the climate device."""
        super().__init__(device.coordinator)
        self.device = device
        self._attr_unique_id = device.unique_id

        # Optimistic state cache - stores values we've commanded but haven't confirmed yet
        self._optimistic_state: dict[str, Any] = {}

        # Set up supported features based on device profile
        self._setup_supported_features()

    def _setup_supported_features(self) -> None:
        """Set up supported features based on device capabilities."""
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile

            # Check for fan speed support
            if profile_data.get("numberOfFanSpeeds", 0) > 0:
                features |= ClimateEntityFeature.FAN_MODE

            # Check for vane/swing support
            if profile_data.get("hasVaneSwing", False):
                features |= ClimateEntityFeature.SWING_MODE

            if profile_data.get("hasVaneDir", False):
                features |= ClimateEntityFeature.SWING_MODE

        self._attr_supported_features = features

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # When we get fresh data from the cloud, clear optimistic values that match
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})

        # Clear optimistic values if cloud state matches what we commanded
        keys_to_remove = []
        for key in list(self._optimistic_state.keys()):
            cloud_value = device_data.get(key, adapter.get(key))
            if cloud_value == self._optimistic_state[key]:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._optimistic_state[key]

        super()._handle_coordinator_update()

    def _get_value(self, key: str) -> Any:
        """Get a value, preferring optimistic state over cloud state."""
        # If we have an optimistic value, use it
        if key in self._optimistic_state:
            return self._optimistic_state[key]

        # Otherwise use cloud data
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        return device_data.get(key, adapter.get(key))

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        zone_data = self.device.zone_data
        device_data = self.device.device_data

        model = device_data.get("model", {}).get("materialDescription", "Unknown Model")

        return DeviceInfo(
            identifiers={(DOMAIN, self.device.device_serial)},
            name=zone_data.get("name", "Kumo Cloud Device"),
            manufacturer="Mitsubishi Electric",
            model=model,
            sw_version=device_data.get("model", {}).get("serialProfile"),
            serial_number=device_data.get("serialNumber"),
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        adapter = self.device.zone_data.get("adapter", {})
        return adapter.get("roomTemp")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        hvac_mode = self.hvac_mode

        if hvac_mode == HVACMode.COOL:
            return self._get_value("spCool")
        elif hvac_mode == HVACMode.HEAT:
            return self._get_value("spHeat")
        elif hvac_mode == HVACMode.HEAT_COOL:
            # For auto mode, could return either cool or heat setpoint
            # Return the cool setpoint as default
            return self._get_value("spCool") or self._get_value("spHeat")

        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        operation_mode = self._get_value("operationMode")
        if operation_mode is None:
            operation_mode = OPERATION_MODE_OFF

        power = self._get_value("power")
        if power is None:
            power = 0

        # If power is 0, device is off regardless of operation mode
        if power == 0:
            return HVACMode.OFF

        return KUMO_TO_HVAC_MODE.get(operation_mode, HVACMode.OFF)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available HVAC modes."""
        modes = [HVACMode.OFF]

        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile

            # Add modes based on device capabilities
            if profile_data.get("hasModeHeat", False):
                modes.append(HVACMode.HEAT)

            modes.append(HVACMode.COOL)  # All units should support cool

            if profile_data.get("hasModeDry", False):
                modes.append(HVACMode.DRY)

            if profile_data.get("hasModeVent", False):
                modes.append(HVACMode.FAN_ONLY)

            # Auto mode if device supports both heat and cool
            if profile_data.get("hasModeHeat", False):
                modes.append(HVACMode.HEAT_COOL)

        return modes

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action based on actual device status."""
        hvac_mode = self.hvac_mode
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        power = self._get_value("power")
        if power is None:
            power = 0
        operation_mode = self._get_value("operationMode")
        if operation_mode is None:
            operation_mode = OPERATION_MODE_OFF

        if power == 0:
            return HVACAction.OFF

        # If device is on and has a valid operation mode, show it as active
        if operation_mode == OPERATION_MODE_HEAT:
            # For heating mode, show as heating if power is on
            return HVACAction.HEATING
        elif operation_mode == OPERATION_MODE_COOL:
            # For cooling mode, show as cooling if power is on
            return HVACAction.COOLING
        elif operation_mode == OPERATION_MODE_DRY:
            return HVACAction.DRYING
        elif operation_mode == OPERATION_MODE_VENT:
            return HVACAction.FAN
        elif operation_mode in (
            OPERATION_MODE_AUTO,
            OPERATION_MODE_AUTO_COOL,
            OPERATION_MODE_AUTO_HEAT,
        ):
            # For auto mode, determine action based on current vs target temperature
            current_temp = self.current_temperature
            target_temp = self.target_temperature

            if current_temp is not None and target_temp is not None:
                temp_diff = current_temp - target_temp
                if temp_diff > 1.0:  # More than 1 degree above target
                    return HVACAction.COOLING
                elif temp_diff < -1.0:  # More than 1 degree below target
                    return HVACAction.HEATING

            # Default to idle for auto mode if we can't determine
            return HVACAction.IDLE

        # If power is on but we can't determine the action, show as idle
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode (translated to user-friendly label)."""
        api_fan_speed = self._get_value("fanSpeed")

        # Translate API value to UI label
        if api_fan_speed:
            return API_TO_UI_FAN.get(api_fan_speed, api_fan_speed)
        return None

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes based on device capabilities."""
        profile = self.device.profile_data
        if not profile:
            return None

        profile_data = profile[0] if isinstance(profile, list) else profile
        num_fan_speeds = profile_data.get("numberOfFanSpeeds", 0)

        if num_fan_speeds == 0:
            return None

        # Return auto plus the number of speeds the device supports
        # Note: numberOfFanSpeeds seems to underreport by 1 for some devices (cassettes)
        # Add +1 to account for this, which gives cassettes: Auto, Quiet, Low, Medium, High
        # And allows wall/floor units with more speeds to show all available options
        modes = [UI_FAN_SPEEDS[0]]  # Always include auto

        # Add speeds based on device capability, with +1 to account for underreporting
        for i in range(1, min(num_fan_speeds + 2, len(UI_FAN_SPEEDS))):
            modes.append(UI_FAN_SPEEDS[i])

        return modes

    @property
    def swing_mode(self) -> str | None:
        """Return current vane position (translated to user-friendly label)."""
        api_vane_position = self._get_value("airDirection")

        # Translate API value to UI label
        if api_vane_position:
            return API_TO_UI_VANE.get(api_vane_position, api_vane_position)
        return None

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the list of available swing modes."""
        profile = self.device.profile_data
        if not profile:
            return None

        profile_data = profile[0] if isinstance(profile, list) else profile

        # If device supports vane control, return all positions
        if profile_data.get("hasVaneDir", False) or profile_data.get("hasVaneSwing", False):
            return UI_VANE_POSITIONS.copy()

        return None

    @property
    def min_temp(self) -> float:
        """Return minimum temperature."""
        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile
            min_setpoints = profile_data.get("minimumSetPoints", {})
            # Return the minimum of heat and cool setpoints
            return min(min_setpoints.get("heat", 16), min_setpoints.get("cool", 16))
        return 16.0

    @property
    def max_temp(self) -> float:
        """Return maximum temperature."""
        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile
            max_setpoints = profile_data.get("maximumSetPoints", {})
            # Return the maximum of heat and cool setpoints
            return max(max_setpoints.get("heat", 30), max_setpoints.get("cool", 30))
        return 30.0

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return 0.5  # Kumo Cloud typically supports 0.5 degree steps

    def _snap_temperature(self, temp: float | None) -> float | None:
        """Snap temperature to nearest 0.5°C increment.

        This ensures temperatures are always on the API's valid grid,
        preventing discrepancies when HA is configured to display in Fahrenheit.
        """
        if temp is None:
            return None
        return round(temp * 2) / 2

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.device.available and self.coordinator.last_update_success

    async def _send_command_and_refresh(self, commands: dict[str, Any]) -> None:
        """Send command, update optimistic state, and refresh."""
        # Store commanded values in optimistic state
        self._optimistic_state.update(commands)

        # Immediately update UI with optimistic values
        self.async_write_ha_state()

        # Send command to device
        await self.device.send_command(commands)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command_and_refresh({"operationMode": OPERATION_MODE_OFF})
        else:
            kumo_mode = HVAC_TO_KUMO_MODE.get(hvac_mode)
            if kumo_mode:
                commands = {"operationMode": kumo_mode}

                # Include current setpoints to maintain them
                sp_cool = self._get_value("spCool")
                sp_heat = self._get_value("spHeat")

                if sp_cool is not None:
                    commands["spCool"] = sp_cool
                if sp_heat is not None:
                    commands["spHeat"] = sp_heat

                await self._send_command_and_refresh(commands)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        target_temp = kwargs.get(ATTR_TEMPERATURE)
        if target_temp is None:
            return

        # Snap to 0.5°C increments for API compatibility
        target_temp = self._snap_temperature(target_temp)

        hvac_mode = self.hvac_mode
        commands = {}

        if hvac_mode == HVACMode.COOL:
            commands["spCool"] = target_temp
            # Maintain heat setpoint
            sp_heat = self._get_value("spHeat")
            if sp_heat is not None:
                commands["spHeat"] = sp_heat
        elif hvac_mode == HVACMode.HEAT:
            commands["spHeat"] = target_temp
            # Maintain cool setpoint
            sp_cool = self._get_value("spCool")
            if sp_cool is not None:
                commands["spCool"] = sp_cool
        elif hvac_mode == HVACMode.HEAT_COOL:
            # For auto mode, set both setpoints based on current temperature
            commands["spCool"] = target_temp
            commands["spHeat"] = self._snap_temperature(target_temp - 2)  # 2 degree hysteresis

        if commands:
            await self._send_command_and_refresh(commands)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode (translate UI label to API value)."""
        # Translate UI label to API value
        api_value = UI_TO_API_FAN.get(fan_mode, fan_mode)
        await self._send_command_and_refresh({"fanSpeed": api_value})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing mode (translate UI label to API value)."""
        # Translate UI label to API value
        api_value = UI_TO_API_VANE.get(swing_mode, swing_mode)
        await self._send_command_and_refresh({"airDirection": api_value})

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        # Turn on with the last used mode, or cool mode if no previous mode
        operation_mode = self._get_value("operationMode")
        if operation_mode is None or operation_mode == OPERATION_MODE_OFF:
            operation_mode = OPERATION_MODE_COOL

        commands = {"operationMode": operation_mode}

        # Include setpoints
        sp_cool = self._get_value("spCool")
        sp_heat = self._get_value("spHeat")

        if sp_cool is not None:
            commands["spCool"] = sp_cool
        if sp_heat is not None:
            commands["spHeat"] = sp_heat

        await self._send_command_and_refresh(commands)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self._send_command_and_refresh({"operationMode": OPERATION_MODE_OFF})
