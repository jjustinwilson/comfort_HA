# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Support for units that report `autoHeat` or `autoCool` operation modes
- HVACMode.HEAT_COOL now activates when these modes are reported
- Granular fan and vane control with user-friendly labels
- Optimistic UI updates for immediate feedback

### Fixed
- Temperature synchronization issue when Home Assistant is configured to display Fahrenheit
  - Added temperature snapping to 0.5°C increments before sending to API
  - Ensures consistent temperature readings across HA, MHK2 thermostats, and Comfort Cloud app
  - Resolves issue where unsnapped Celsius conversions (e.g., 18.8889°C for 66°F) caused mismatches
- Multiple sites configuration issue

## [1.0.0] - 2024-01-01

### Added
- Initial release of Mitsubishi Comfort integration
- Climate control support for Mitsubishi Electric systems via Kumo Cloud API
- Config flow for easy setup
- Multi-zone support
- Automatic token refresh
- Device capability detection
- Support for temperature, HVAC modes, fan speeds, and air direction
- Real-time temperature and humidity monitoring

### Features
- Climate entity with full Home Assistant integration
- Automatic discovery of zones within selected site
- Configurable update intervals
- Error handling and retry logic
- Support for multiple HVAC modes (heat, cool, dry, fan, auto)
- Device-specific feature detection 