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
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
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
    FAN_SPEED_AUTO,
    FAN_SPEED_LOW,
    FAN_SPEED_MEDIUM,
    FAN_SPEED_HIGH,
    AIR_DIRECTION_HORIZONTAL,
    AIR_DIRECTION_VERTICAL,
    AIR_DIRECTION_SWING,
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
HVAC_TO_KUMO_MODE = {
    HVACMode.OFF: OPERATION_MODE_OFF,
    HVACMode.COOL: OPERATION_MODE_COOL,
    HVACMode.HEAT: OPERATION_MODE_HEAT,
    HVACMode.DRY: OPERATION_MODE_DRY,
    HVACMode.FAN_ONLY: OPERATION_MODE_VENT,
    HVACMode.HEAT_COOL: OPERATION_MODE_AUTO,
}

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

            # Check if device supports both heat and cool (for dual setpoint support)
            if profile_data.get("hasModeHeat", False):
                features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

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

        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return the upper bound temperature for heat/cool mode."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            adapter = self.device.zone_data.get("adapter", {})
            device_data = self.device.device_data
            # Use device data if available (more current), otherwise use adapter data
            return device_data.get("spCool", adapter.get("spCool"))
        return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lower bound temperature for heat/cool mode."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            adapter = self.device.zone_data.get("adapter", {})
            device_data = self.device.device_data
            # Use device data if available (more current), otherwise use adapter data
            return device_data.get("spHeat", adapter.get("spHeat"))
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
        elif operation_mode in (
            OPERATION_MODE_AUTO,
            OPERATION_MODE_AUTO_COOL,
            OPERATION_MODE_AUTO_HEAT,
        ):
            # For auto mode, determine action based on current vs target temperature
            current_temp = self.current_temperature
            
            if current_temp is not None:
                # For autoCool/autoHeat, use the specific mode's action
                if operation_mode == OPERATION_MODE_AUTO_COOL:
                    target_temp = self.target_temperature_high
                    if target_temp is not None and current_temp > target_temp + 0.5:
                        return HVACAction.COOLING
                elif operation_mode == OPERATION_MODE_AUTO_HEAT:
                    target_temp = self.target_temperature_low
                    if target_temp is not None and current_temp < target_temp - 0.5:
                        return HVACAction.HEATING
                else:
                    # For generic auto mode, check both setpoints
                    high_temp = self.target_temperature_high
                    low_temp = self.target_temperature_low
                    
                    if high_temp is not None and current_temp > high_temp + 0.5:
                        return HVACAction.COOLING
                    elif low_temp is not None and current_temp < low_temp - 0.5:
                        return HVACAction.HEATING

            # Default to idle for auto mode if we can't determine
            return HVACAction.IDLE

        # If power is on but we can't determine the action, show as idle
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode."""
        # Check device data first, then adapter data
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        return device_data.get("fanSpeed", adapter.get("fanSpeed"))

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes."""
        profile = self.device.profile_data
        if not profile:
            return None

        profile_data = profile[0] if isinstance(profile, list) else profile
        num_fan_speeds = profile_data.get("numberOfFanSpeeds", 0)

        if num_fan_speeds == 0:
            return None

        # Return fan modes based on number of speeds supported
        modes = [FAN_SPEED_AUTO]
        if num_fan_speeds >= 1:
            modes.append(FAN_SPEED_LOW)
        if num_fan_speeds >= 2:
            modes.append(FAN_SPEED_MEDIUM)
        if num_fan_speeds >= 3:
            modes.append(FAN_SPEED_HIGH)

        return modes

    @property
    def swing_mode(self) -> str | None:
        """Return current swing mode."""
        # Check device data first, then adapter data
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        return device_data.get("airDirection", adapter.get("airDirection"))

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the list of available swing modes."""
        profile = self.device.profile_data
        if not profile:
            return None

        profile_data = profile[0] if isinstance(profile, list) else profile

        modes = []
        if profile_data.get("hasVaneDir", False) or profile_data.get(
            "hasVaneSwing", False
        ):
            modes.extend(KUMO_AIR_DIRECTIONS)

        return modes if modes else None

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
        # Cache the command first
        self.device.cache_commands(commands)

        # Trigger a state update for this entity to reflect cached changes
        self.async_write_ha_state()

        # Send the command and refresh the device
        await self.device.send_command(commands)

        # Trigger another state update to reflect refreshed changes
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
        hvac_mode = self.hvac_mode
        commands = {}

        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        if hvac_mode == HVACMode.HEAT_COOL:
            # Handle dual setpoint mode
            target_temp_high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
            target_temp_low = kwargs.get(ATTR_TARGET_TEMP_LOW)
            
            if target_temp_high is not None:
                commands["spCool"] = target_temp_high
            else:
                # Maintain existing high setpoint
                sp_cool = device_data.get("spCool", adapter.get("spCool"))
                if sp_cool is not None:
                    commands["spCool"] = sp_cool
                    
            if target_temp_low is not None:
                commands["spHeat"] = target_temp_low
            else:
                # Maintain existing low setpoint
                sp_heat = device_data.get("spHeat", adapter.get("spHeat"))
                if sp_heat is not None:
                    commands["spHeat"] = sp_heat
        else:
            # Handle single setpoint modes
            target_temp = kwargs.get(ATTR_TEMPERATURE)
            if target_temp is None:
                return

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

        if commands:
            await self._send_command_and_refresh(commands)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        await self._send_command_and_refresh({"fanSpeed": fan_mode})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing mode."""
        await self._send_command_and_refresh({"airDirection": swing_mode})

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
