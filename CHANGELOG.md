# Changelog

## [1.3.0] - 2026-06-02

### Added
- Versioned log output: all log messages prefixed with `[vN]` via `_VersionedLogger` adapter for easy correlation across HA log files

### Changed
- All state reads (`current_temperature`, `target_temperature`, `hvac_mode`, `hvac_action`, `fan_mode`, `swing_mode`) now read exclusively from `device_data` (GET /devices/{serial}) — the fresher, more complete source
- Adapter zone data is no longer written back during command dispatch or post-command refresh
- Command cache eviction replaced with a 60-second TTL (previously compared against server `updatedAt` timestamp, would often lag)
- lock and 5-second minimum gap between consecutive commands to the same device — prevents duplicate or out-of-order commands from rapid UI changes
- `available()` now checks for cached zone/device data in memory rather than gating on `last_update_success`, keeping entities available through transient API poll failures
- `temperature_unit` reads from `hass.config.units.temperature_unit` at runtime (previously computed once at setup)
- HVAC action dead-band is unit-aware: 1.0 °F in Fahrenheit mode, 0.5 °C in Celsius mode

### Fixed
- `roomTemp`, `spCool`, `spHeat` were incorrectly read from the zone adapter instead of device_data, causing temperature display lag and setpoint flicker after changes
- Transient per-device fetch failures (500s, timeouts) no longer wipe device state; previous device_data is preserved until a successful poll

## [1.1.0] - 2026-03-09

### Added
- Mitsubishi proprietary F/C temperature lookup tables (ekiczek PR #23, PR #199)
- Fan speed mapping: API values now correctly translate to Comfort app labels
- Vane position mapping: API values now correctly translate to Comfort app labels
- Command caching with `updatedAt` comparison to prevent state bounce (smack000)
- Standalone temperature and humidity sensor entities per zone (smack000)
- Wireless sensor support: battery level, signal strength (RSSI), temperature, and humidity
  from PAC-USWHS003-TH-1 sensors via /v3/devices/{serial}/sensor endpoint
- Diagnostic sensors: WiFi adapter firmware version and signal strength via /v3/devices/{serial}/status
- Filter maintenance tracking via /v3/zones/{id}/notification-preferences
- Updated API app version from 3.0.9 to 3.2.4 to match current Comfort app
- Auto heat/cool mode with dual setpoint support (smack000 / tw3rp)
- Refactored architecture: API client and coordinator in separate modules (smack000)
- API retry logic with exponential backoff for 429 rate limits (smack000 / tw3rp)
- Improved entity availability: prevents false automation triggers during transient API errors (tw3rp)
- Debug logging for fan speed and vane position translations

### Fixed
- Temperature setpoints now match the Comfort app exactly (no more ~1 F drift)
- Fan speed display matches Comfort app labels (was showing raw API values)
- Vane position display matches Comfort app labels (was showing raw API values)
- State bouncing after sending commands (cached commands maintained until server confirms)
- Sensor entities now inherit from CoordinatorEntity for automatic updates

## [0.1.1-alpha.1] - Previous upstream release
- Initial Kumo Cloud V3 API integration by jjustinwilson
