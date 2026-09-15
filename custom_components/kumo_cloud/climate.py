"""Platform for Kumo Cloud climate integration.

Merged from multiple forks:
- smack000: Command caching, anti-bounce, coordinator refactor, auto heat/cool,
  humidity attribute, power-based off detection
- ekiczek: Mitsubishi proprietary F/C temperature lookup tables (PR #23, PR #199)
- tw3rp: Dual setpoint support, improved entity availability, API rate limiting
- Fan/vane UI mapping: Correct Comfort app labels for fan speeds and vane positions
"""

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

from .coordinator import KumoCloudDataUpdateCoordinator, KumoCloudDevice
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


# =============================================================================
# Mitsubishi proprietary F<->C temperature conversion
# =============================================================================
# Mitsubishi systems use 0.5 C steps internally, but their F-to-C mapping
# diverges from standard math at several points (64-66 F and 69-72 F).
# This lookup table matches the Comfort app and physical thermostat exactly,
# eliminating the ~1 F drift that standard rounding causes for Fahrenheit users.
# Source: ekiczek/comfort_HA PR #23, dlarrick/hass-kumo PR #199

_F_TO_C: dict[int, float] = {
    61: 16.0, 62: 16.5, 63: 17.0, 64: 17.5, 65: 18.0, 66: 18.5,
    67: 19.5, 68: 20.0, 69: 21.0, 70: 21.5, 71: 22.0, 72: 22.5,
    73: 23.0, 74: 23.5, 75: 24.0, 76: 24.5, 77: 25.0, 78: 25.5,
    79: 26.0, 80: 26.5,
}

# Celsius-to-Fahrenheit lookup for display. This is NOT a simple inverse of
# _F_TO_C because Mitsubishi's C->F mapping for room temperature display
# differs from the setpoint mapping at certain values (e.g. 19.0 C = 67 F
# for display, but 67 F = 19.5 C for setpoints).
_C_TO_F: dict[float, int] = {
    16.0: 61, 16.5: 62, 17.0: 63, 17.5: 64, 18.0: 65, 18.5: 66,
    19.0: 67, 19.5: 67, 20.0: 68, 20.5: 69,
    21.0: 69, 21.5: 70, 22.0: 71, 22.5: 72,
    23.0: 73, 23.5: 74, 24.0: 75, 24.5: 76, 25.0: 77, 25.5: 78,
    26.0: 79, 26.5: 80,
}


def _c_to_f(celsius: float | None) -> float | None:
    """Convert Celsius to Fahrenheit using Mitsubishi's lookup table.

    Falls back to standard rounding for values outside the table
    (e.g. current room temperature readings that may not be exact setpoints).
    """
    if celsius is None:
        return None
    if celsius in _C_TO_F:
        return _C_TO_F[celsius]
    return round(celsius * 9.0 / 5.0 + 32.0)


def _f_to_c(fahrenheit: float | None) -> float | None:
    """Convert Fahrenheit to Celsius using Mitsubishi's lookup table.

    Falls back to standard rounding for values outside the table.
    """
    if fahrenheit is None:
        return None
    f_int = int(round(fahrenheit))
    if f_int in _F_TO_C:
        return _F_TO_C[f_int]
    celsius = (fahrenheit - 32.0) * 5.0 / 9.0
    return round(celsius * 2.0) / 2.0


# =============================================================================
# Fan speed mapping: Kumo Cloud API values <-> Comfort app UI labels
# =============================================================================
# The V3 API uses internal speed names that don't match what the Comfort app
# or physical remote displays. This mapping translates between the two.

API_TO_UI_FAN = {
    "auto": "auto",
    "superQuiet": "quiet",       # vendor "superQuiet"    -> UI "quiet"
    "quiet": "low",              # vendor "quiet"         -> UI "low"
    "low": "medium",             # vendor "low"           -> UI "medium"
    "powerful": "high",          # vendor "powerful"      -> UI "high"
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
# Order matters for HomeKit bucketing; keep low->high progression
UI_FAN_ORDER = ["auto", "quiet", "low", "medium", "high", "powerful"]


# =============================================================================
# Vane (air direction) mapping: Kumo Cloud API values <-> Comfort app UI labels
# =============================================================================

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
def _debug(msg: str, *args: Any) -> None:
    """Log only when HA logger is set to DEBUG for this component."""
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(msg, *args)


# =============================================================================
# HVAC mode mappings
# =============================================================================

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

HVAC_TO_KUMO_MODE = {
    HVACMode.OFF: OPERATION_MODE_OFF,
    HVACMode.COOL: OPERATION_MODE_COOL,
    HVACMode.HEAT: OPERATION_MODE_HEAT,
    HVACMode.DRY: OPERATION_MODE_DRY,
    HVACMode.FAN_ONLY: OPERATION_MODE_VENT,
    HVACMode.HEAT_COOL: OPERATION_MODE_AUTO,
}

# Legacy constants kept for reference
KUMO_FAN_SPEEDS = [FAN_SPEED_AUTO, FAN_SPEED_LOW, FAN_SPEED_MEDIUM, FAN_SPEED_HIGH]
KUMO_AIR_DIRECTIONS = [AIR_DIRECTION_HORIZONTAL, AIR_DIRECTION_VERTICAL, AIR_DIRECTION_SWING]


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

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
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

    # ---- Device info --------------------------------------------------------

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

    # ---- Temperature properties ---------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature (converted to Fahrenheit)."""
        adapter = self.device.zone_data.get("adapter", {})
        return _c_to_f(adapter.get("roomTemp"))

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature for single-setpoint modes."""
        adapter = self.device.zone_data.get("adapter", {})
        hvac_mode = self.hvac_mode

        if hvac_mode == HVACMode.COOL:
            return _c_to_f(adapter.get("spCool"))
        elif hvac_mode == HVACMode.HEAT:
            return _c_to_f(adapter.get("spHeat"))

        # HEAT_COOL uses target_temperature_high/low instead
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return the upper bound temperature for heat/cool mode."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            adapter = self.device.zone_data.get("adapter", {})
            device_data = self.device.device_data
            return _c_to_f(device_data.get("spCool", adapter.get("spCool")))
        return None

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lower bound temperature for heat/cool mode."""
        if self.hvac_mode == HVACMode.HEAT_COOL:
            adapter = self.device.zone_data.get("adapter", {})
            device_data = self.device.device_data
            return _c_to_f(device_data.get("spHeat", adapter.get("spHeat")))
        return None

    @property
    def min_temp(self) -> float:
        """Return minimum temperature."""
        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile
            min_setpoints = profile_data.get("minimumSetPoints", {})
            min_c = min(min_setpoints.get("heat", 16), min_setpoints.get("cool", 16))
            return _c_to_f(min_c)
        return _c_to_f(16.0)

    @property
    def max_temp(self) -> float:
        """Return maximum temperature."""
        profile = self.device.profile_data
        if profile:
            profile_data = profile[0] if isinstance(profile, list) else profile
            max_setpoints = profile_data.get("maximumSetPoints", {})
            max_c = max(max_setpoints.get("heat", 30), max_setpoints.get("cool", 30))
            return _c_to_f(max_c)
        return _c_to_f(30.0)

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return 1.0  # 1 F steps (maps to ~0.5 C steps internally)

    # ---- HVAC mode ----------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

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
            max_setpoints = profile_data.get("maximumSetPoints", {})

            if profile_data.get("hasModeHeat", False) or "heat" in max_setpoints:
                modes.append(HVACMode.HEAT)
            if profile_data.get("hasModeCool", False) or "cool" in max_setpoints:
                modes.append(HVACMode.COOL)
            if profile_data.get("hasModeDry", False):
                modes.append(HVACMode.DRY)
            if profile_data.get("hasModeFan", False) or profile_data.get("hasModeVent", False):
                modes.append(HVACMode.FAN_ONLY)
            if profile_data.get("hasModeAuto", False) or "auto" in max_setpoints:
                modes.append(HVACMode.HEAT_COOL)
        else:
            modes.extend([HVACMode.HEAT, HVACMode.COOL])

        return modes

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running HVAC operation."""
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})

        operation_mode = device_data.get(
            "operationMode", adapter.get("operationMode", OPERATION_MODE_OFF)
        )
        power = device_data.get("power", adapter.get("power", 0))

        if operation_mode == OPERATION_MODE_OFF or power == 0:
            return HVACAction.OFF

        if operation_mode == OPERATION_MODE_COOL:
            return HVACAction.COOLING
        elif operation_mode == OPERATION_MODE_HEAT:
            return HVACAction.HEATING
        elif operation_mode == OPERATION_MODE_DRY:
            return HVACAction.DRYING
        elif operation_mode == OPERATION_MODE_VENT:
            return HVACAction.FAN
        elif operation_mode in (OPERATION_MODE_AUTO, OPERATION_MODE_AUTO_COOL, OPERATION_MODE_AUTO_HEAT):
            if operation_mode == OPERATION_MODE_AUTO_COOL:
                return HVACAction.COOLING
            elif operation_mode == OPERATION_MODE_AUTO_HEAT:
                return HVACAction.HEATING

            # Generic auto: infer from temperature difference
            current_temp = self.current_temperature
            target_temp = self.target_temperature_high or self.target_temperature

            if current_temp is not None and target_temp is not None:
                temp_diff = current_temp - target_temp
                if temp_diff > 1.0:
                    return HVACAction.COOLING
                elif temp_diff < -1.0:
                    return HVACAction.HEATING

            return HVACAction.IDLE

        return HVACAction.IDLE

    # ---- Fan mode -----------------------------------------------------------

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode (canonical lowercase UI label)."""
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        fan_speed = device_data.get("fanSpeed", adapter.get("fanSpeed"))
        _debug("API returned fanSpeed for %s: %s", self.device.device_serial, fan_speed)
        ui_label = API_TO_UI_FAN.get(fan_speed, fan_speed)
        _debug("HA presenting fan mode for %s as: %s", self.device.device_serial, ui_label)
        return ui_label

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        return UI_FAN_ORDER.copy()

    # ---- Swing (vane) mode --------------------------------------------------

    @property
    def swing_mode(self) -> str | None:
        """Return current vane position (canonical lowercase UI label)."""
        device_data = self.device.device_data
        adapter = self.device.zone_data.get("adapter", {})
        swing = device_data.get("airDirection", adapter.get("airDirection"))
        _debug("API returned airDirection for %s: %s", self.device.device_serial, swing)
        return API_TO_UI_VANE.get(swing, swing)

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

    # ---- Misc properties ----------------------------------------------------

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity from the V3 device or adapter payload."""
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data
        return device_data.get("humidity", adapter.get("humidity"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the optional state attributes."""
        attributes = super().extra_state_attributes or {}

        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data
        humidity = device_data.get("humidity", adapter.get("humidity"))
        if humidity is not None:
            attributes["humidity"] = humidity

        return attributes

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Keep entity available when we have data even if the last poll failed.
        This prevents automations from triggering spuriously when the entity
        flickers between available/unavailable due to transient API errors.
        """
        has_data = (
            self.device.zone_data
            and self.device.device_data
            and self.coordinator.data is not None
        )
        return has_data and self.device.available

    # ---- Commands -----------------------------------------------------------

    async def _send_command_and_refresh(self, commands: dict[str, Any]) -> None:
        """Send command, cache it to prevent bounce, and refresh."""
        _debug("HA sending command to %s: %s", self.device.device_serial, commands)

        # Cache the command first so UI updates immediately
        self.device.cache_commands(commands)
        self.async_write_ha_state()

        # Send the command and refresh the device
        await self.device.send_command(commands)

        # Write state again after refresh
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

                sp_cool = device_data.get("spCool", adapter.get("spCool"))
                sp_heat = device_data.get("spHeat", adapter.get("spHeat"))

                if sp_cool is not None:
                    commands["spCool"] = sp_cool
                if sp_heat is not None:
                    commands["spHeat"] = sp_heat

                await self._send_command_and_refresh(commands)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data
        commands: dict[str, Any] = {}

        # Range set (preferred for HEAT_COOL / auto)
        low_f = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high_f = kwargs.get(ATTR_TARGET_TEMP_HIGH)

        if low_f is not None or high_f is not None:
            current_low_c = device_data.get("spHeat", adapter.get("spHeat"))
            current_high_c = device_data.get("spCool", adapter.get("spCool"))

            if low_f is not None:
                commands["spHeat"] = _f_to_c(low_f)
            elif current_low_c is not None:
                commands["spHeat"] = current_low_c

            if high_f is not None:
                commands["spCool"] = _f_to_c(high_f)
            elif current_high_c is not None:
                commands["spCool"] = current_high_c

            if commands:
                await self._send_command_and_refresh(commands)
            return

        # Single setpoint (heat/cool modes)
        target_temp_f = kwargs.get(ATTR_TEMPERATURE)
        if target_temp_f is None:
            return

        target_temp_c = _f_to_c(target_temp_f)
        hvac_mode = self.hvac_mode

        if hvac_mode == HVACMode.COOL:
            commands["spCool"] = target_temp_c
            sp_heat = device_data.get("spHeat", adapter.get("spHeat"))
            if sp_heat is not None:
                commands["spHeat"] = sp_heat

        elif hvac_mode == HVACMode.HEAT:
            commands["spHeat"] = target_temp_c
            sp_cool = device_data.get("spCool", adapter.get("spCool"))
            if sp_cool is not None:
                commands["spCool"] = sp_cool

        elif hvac_mode == HVACMode.HEAT_COOL:
            # Single setpoint in auto mode: set both with hysteresis
            commands["spCool"] = target_temp_c
            commands["spHeat"] = target_temp_c - 1.0  # ~2 F hysteresis

        if commands:
            await self._send_command_and_refresh(commands)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode (accepts UI label, sends API value)."""
        api_value = UI_TO_API_FAN.get(fan_mode.lower(), fan_mode)
        _debug("Setting fan mode: UI '%s' -> API '%s'", fan_mode, api_value)
        await self._send_command_and_refresh({"fanSpeed": api_value})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set vane position (accepts UI label, sends API value)."""
        api_value = UI_TO_API_VANE.get(swing_mode.lower(), swing_mode)
        _debug("Setting swing mode: UI '%s' -> API '%s'", swing_mode, api_value)
        await self._send_command_and_refresh({"airDirection": api_value})

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        adapter = self.device.zone_data.get("adapter", {})
        device_data = self.device.device_data

        operation_mode = device_data.get(
            "operationMode", adapter.get("operationMode", OPERATION_MODE_COOL)
        )

        if operation_mode == OPERATION_MODE_OFF:
            operation_mode = OPERATION_MODE_COOL

        commands = {"operationMode": operation_mode}

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
