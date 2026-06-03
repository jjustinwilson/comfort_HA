# Mitsubishi Comfort Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

Fork of [jjustinwilson/comfort_HA](https://github.com/jjustinwilson/comfort_HA) with the following fixes and enhancements:

## What's different in this fork

### TTL-based command cache eviction
Justin's fork culls cached commands by comparing against the `updatedAt` timestamp returned by the server — which can lag or be absent. This fork replaces that with a simple 60-second TTL: cached commands are evicted 60 seconds after they were sent, regardless of server state. More reliable in practice and easier to reason about.

### Serialized command dispatch with rate limiting
A `_send_lock` ensures only one command is in flight at a time, and a 5-second minimum gap is enforced between consecutive commands to the same device. Prevents duplicate or out-of-order commands when the user rapidly changes settings in the UI.

### Resilient entity availability
`available()` checks whether cached zone/device data exists in memory rather than gating on `last_update_success`. This keeps entities available and showing their last-known state through transient API poll failures, instead of going unavailable and losing the current state display.

### Dynamic temperature unit
`temperature_unit` reads from `hass.config.units.temperature_unit` at runtime rather than being computed once at setup. Correctly reflects any change to HA's unit system without requiring a restart.

### Unit-aware HVAC action dead-band
The dead-band used to determine whether the unit is actively heating or cooling is 1.0 °F when HA is configured in Fahrenheit and 0.5 °C in Celsius, matching the resolution of the Kumo Cloud API in each unit system.

### Single source of truth for device state
All state properties (temperature, setpoints, HVAC mode, fan speed, vane position) read exclusively from `GET /devices/{serial}` — the fresher, more complete API endpoint. Zone adapter data (`GET /zones`) is no longer used for state reads. Transient fetch failures preserve the last-known device state rather than blanking it out.

### Versioned log output
All log messages are prefixed with `[vN]` (where N is `LOG_VERSION` in `const.py`) via a `_VersionedLogger` adapter. Makes it straightforward to correlate log entries from a specific deployment when troubleshooting across HA log files.

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz) if you haven't already
2. Go to HACS > Integrations > 3 dots menu > Custom repositories
3. Add `JoeQuantum/comfort_HA` with category "Integration"
4. Search for "Mitsubishi Comfort" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/kumo_cloud` folder to your HA `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings > Devices & Services > Add Integration
2. Search for "Mitsubishi Comfort"
3. Enter your Kumo Cloud / Comfort app credentials
4. Select your site if you have multiple

## Fan Speed Reference

| HA Label | Comfort App | API Value |
|----------|-------------|-----------|
| auto     | Auto        | auto      |
| quiet    | Quiet       | superQuiet |
| low      | Low         | quiet     |
| medium   | Medium      | low       |
| high     | High        | powerful  |
| powerful | Powerful    | superPowerful |

## Vane Position Reference

| HA Label | Comfort App | API Value |
|----------|-------------|-----------|
| auto     | Auto        | auto      |
| swing    | Swing       | swing     |
| lowest   | Lowest      | vertical  |
| low      | Low         | midvertical |
| middle   | Middle      | midpoint  |
| high     | High        | midhorizontal |
| highest  | Highest     | horizontal |

## Credits

- [jjustinwilson](https://github.com/jjustinwilson/comfort_HA) - Original integration and V3 API reverse engineering
- [ekiczek](https://github.com/ekiczek/comfort_HA) - Mitsubishi F/C temperature lookup tables (PR #23, hass-kumo PR #199)
- [smack000](https://github.com/smack000/comfort_HA) - Command caching, coordinator refactor, sensors, auto heat/cool mode
- [tw3rp](https://github.com/jjustinwilson/comfort_HA/pull/2#issuecomment-2974732965) - Dual setpoint support for auto heat/cool, improved entity availability, API rate limiting with exponential backoff
