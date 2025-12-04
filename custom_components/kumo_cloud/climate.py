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
    FAN_SPEED_AUTO,
    FAN_SPEED_LOW,
    FAN_SPEED_MEDIUM,
    FAN_SPEED_HIGH,
    AIR_DIRECTION_HORIZONTAL,
    AIR_DIRECTION_VERTICAL,
    AIR_DIRECTION_SWING,
)

_LOGGER = logging.getLogger(__name__)

# === Canonical UI mappings (lowercase labels shown in HA & accepted by HomeKit) ===
# Fan speed: map Kumo API strings <-> canonical UI labels (lowercase)
API_TO_UI_FAN = {
    "auto": "auto",
    "superQuiet": "quiet",       # vendor "superQuiet" -> UI "quiet"
    "quiet": "low",              # vendor "quiet"      -> UI "low"
    "low": "medium",             # vendor "low"        -> UI "medium"
    "powerful": "high",          # vendor "powerful"   -> UI "high"
    "superPowerful": "powerful", # vendor "superPowerful" -> UI "powerful"
}
UI_TO_API_FAN = {
    "auto": "auto",
    "quiet": "superQuiet",
    "low": "quiet",
    "medium": "low",
    "high": "powerful",
    "powerful": "superPowerful",
}
# Order matters for HomeKit bucketing; keep low→high progression
UI_FAN_ORDER = ["auto", "quiet", "low", "medium", "high", "powerful"]

# Vane (air direction): map Kumo API strings <-> canonical UI labels (lowercase)
API_TO_UI_VANE = {
    "auto": "auto",
    "swing": "swing",
    "vertical": "lowest",
    "midvertical": "low",
    "midpoint": "middle",
    "midhorizontal": "high",
    "horizontal": "highest",
}
UI_TO_API_VANE = {
    "auto": "auto",
    "swing": "swing",
    "lowest": "vertical",
    "low": "midvertical",
    "middle": "midpoint",
    "high": "midhorizontal",
    "highest": "horizontal",
}
UI_VANE_ORDER = ["auto", "swing", "lowest", "low", "middle", "high", "highest"]

# Debug logging follows HA's log level (no hardcoded flag needed).
def debug_log(msg: str, *args: Any) -> None:
    """Log message only when HA logger is set to DEBUG for this component."""
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(msg, *args)

# Mapping from Kumo Cloud operation modes to Home Assistant HVAC modes
KUMO_TO_HVAC_MODE = {
    OPERATION_MODE_OFF: HVACMode.OFF,
    OPERATION_MODE_COOL: HVACMode.COOL,
    OPERATION_MODE_HEAT: HVACMode.HEAT,
    OPERATION_MODE_DRY: HVACMode.DRY,
    OPERATION_MODE_VENT: HVACMode.FAN_ONLY,
    OPERATION_MODE_AUTO: HVACMode.HEAT_COOL,
}

# Reverse mapping
HVAC_TO_KUMO_MODE = {v: k for k, v in KUMO_TO_HVAC_MODE.items()}

# Fan speed mappings
KUMO_FAN_SPEEDS = [FAN_SPEED_AUTO, FAN_SPEED_LOW, FAN_SPEED_MEDIUM, FAN_SPEED_HIGH]

# Air direction mappings
KUMO_AIR_DIRECTIONS = [
    AIR_DIRECTION_HORIZONTAL,
    AIR_DIRECTION_VERTICAL,
    AIR_DIRECTION_SWING,
]


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
        adapter = self.device.zone_data.get("adapter", {})
        hvac_mode = self.hvac_mode

        if hvac_mode == HVACMode.COOL:
            return adapter.get("spCool")
        elif hvac_mode == HVACMode.HEAT:
            return adapter.get("spHeat")
        elif hvac_mode == HVACMode.HEAT_COOL:
            # For auto mode, could return either cool or heat setpoint
            # Return the cool setpoint as default
            return adapter.get("spCool") or adapter.get("spHeat")

        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        # Check both adapter (zone) and device data for most current status
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        # Use device data if available (more current), otherwise use adapter data
        operation_mode = device_data.get(
            "operationMode", adapter.get("operationMode", OPERATION_MODE_OFF)
        )
        power = device_data.get("power", adapter.get("power", 0))

        debug_log(
            "API returned for %s: operationMode=%s, power=%s",
            self.device.device_serial,
            operation_mode,
            power,
        )

        # If power is 0, device is off regardless of operation mode
        if power == 0:
            return HVACMode.OFF

        hvac_mode = KUMO_TO_HVAC_MODE.get(operation_mode, HVACMode.OFF)
        debug_log(
            "HA computed hvac_mode for %s: %s",
            self.device.device_serial,
            hvac_mode,
        )
        return hvac_mode

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

        # Check both adapter (zone) and device data for most current status
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        # Use device data if available (more current), otherwise use adapter data
        power = device_data.get("power", adapter.get("power", 0))
        operation_mode = device_data.get(
            "operationMode", adapter.get("operationMode", OPERATION_MODE_OFF)
        )

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
        elif operation_mode == OPERATION_MODE_AUTO:
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
        """Return current fan mode (canonical lowercase label)."""
        # Check device data first, then adapter data
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        fan_speed = device_data.get("fanSpeed", adapter.get("fanSpeed"))
        debug_log(
            "API returned fanSpeed for %s: %s",
            self.device.device_serial,
            fan_speed,
        )
        ui_label = API_TO_UI_FAN.get(fan_speed, fan_speed)
        debug_log(
            "HA presenting fan mode for %s as: %s",
            self.device.device_serial,
            ui_label,
        )
        return ui_label

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        return UI_FAN_ORDER.copy()

    @property
    def swing_mode(self) -> str | None:
        """Return current vane position (canonical lowercase label)."""
        # Check device data first, then adapter data
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        swing = device_data.get("airDirection", adapter.get("airDirection"))
        debug_log(
            "API returned airDirection for %s: %s",
            self.device.device_serial,
            swing,
        )
        ui_label = API_TO_UI_VANE.get(swing, swing)
        return ui_label
    
    @property
    def swing_modes(self) -> list[str] | None:
        """Return the list of available swing modes."""
        profile = self.device.profile_data
        if not profile:
            return None

        profile_data = profile[0] if isinstance(profile, list) else profile
        if not (profile_data.get("hasVaneDir", False) or profile_data.get("hasVaneSwing", False)):
            return None

        return UI_VANE_ORDER.copy()

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

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.device.available and self.coordinator.last_update_success

    async def _send_command_and_refresh(self, commands: dict[str, Any]) -> None:
        """Send command and ensure fresh status update."""
        debug_log(
            "HA sending command to %s: %s",
            self.device.device_serial,
            commands,
        )
        await self.device.send_command(commands)
        # The device.send_command method now handles refreshing the device status
        # Also trigger a state update for this entity to reflect changes immediately
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command_and_refresh({"operationMode": OPERATION_MODE_OFF})
        else:
            kumo_mode = HVAC_TO_KUMO_MODE.get(hvac_mode)
            if kumo_mode:
                commands = {"operationMode": kumo_mode}

                # Include current setpoints to maintain them
                adapter = self.device.zone_data.get("adapter", {})
                device_data = self.device.device_data

                # Use device data if available, otherwise adapter data
                sp_cool = device_data.get("spCool", adapter.get("spCool"))
                sp_heat = device_data.get("spHeat", adapter.get("spHeat"))

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

        hvac_mode = self.hvac_mode
        commands = {}

        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        if hvac_mode == HVACMode.COOL:
            commands["spCool"] = target_temp
            # Maintain heat setpoint
            sp_heat = device_data.get("spHeat", adapter.get("spHeat"))
            if sp_heat is not None:
                commands["spHeat"] = sp_heat
        elif hvac_mode == HVACMode.HEAT:
            commands["spHeat"] = target_temp
            # Maintain cool setpoint
            sp_cool = device_data.get("spCool", adapter.get("spCool"))
            if sp_cool is not None:
                commands["spCool"] = sp_cool
        elif hvac_mode == HVACMode.HEAT_COOL:
            # For auto mode, set both setpoints based on current temperature
            commands["spCool"] = target_temp
            commands["spHeat"] = target_temp - 2  # 2 degree hysteresis

        if commands:
            await self._send_command_and_refresh(commands)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode (accept Mitsubishi label)."""
        api_value = UI_TO_API_FAN.get(fan_mode, fan_mode)
        await self._send_command_and_refresh({"fanSpeed": api_value})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set vane position (accept Mitsubishi label)."""
        api_value = UI_TO_API_VANE.get(swing_mode, swing_mode)
        await self._send_command_and_refresh({"airDirection": api_value})

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        # Turn on with the last used mode, or cool mode if no previous mode
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        # Use device data if available, otherwise adapter data
        operation_mode = device_data.get(
            "operationMode", adapter.get("operationMode", OPERATION_MODE_COOL)
        )

        # If the operation mode is "off", default to cool
        if operation_mode == OPERATION_MODE_OFF:
            operation_mode = OPERATION_MODE_COOL

        commands = {"operationMode": operation_mode}

        # Include setpoints
        sp_cool = device_data.get("spCool", adapter.get("spCool"))
        sp_heat = device_data.get("spHeat", adapter.get("spHeat"))

        if sp_cool is not None:
            commands["spCool"] = sp_cool
        if sp_heat is not None:
            commands["spHeat"] = sp_heat

        await self._send_command_and_refresh(commands)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self._send_command_and_refresh({"operationMode": OPERATION_MODE_OFF})
