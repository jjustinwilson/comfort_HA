# Mitsubishi Comfort Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/jjustinwilson/kumo_cloud.svg)](https://github.com/jjustinwilson/kumo_cloud/releases)

This integration allows you to control your Mitsubishi Electric climate control systems through the Kumo Cloud API in Home Assistant.

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Go to HACS → Integrations
3. Click the three dots in the top right corner and select "Custom repositories"
4. Add this repository URL: `https://github.com/jjustinwilson/comfort_HA/`
5. Select "Integration" as the category
6. Click "Add"
7. Find "Mitsubishi Comfort" in the HACS integrations list and install it
8. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "Mitsubishi Comfort"
3. Enter your Kumo Cloud account credentials (email and password)
4. Select the site you want to control if you have multiple sites
5. Your climate devices will be automatically discovered and added

## Features

- **Climate Control**: Control temperature, HVAC modes (heat, cool, dry, fan, auto), fan speeds, and air direction
- **Real-time Updates**: Monitor current temperature, humidity, and device status
- **Multi-Zone Support**: Control multiple zones within a site
- **Automatic Token Refresh**: Handles authentication token refresh automatically
- **Device Profiles**: Automatically detects device capabilities and adjusts available features
- **Auto Heat/Cool Boundary**: Supports units reporting `autoHeat` or `autoCool` modes with upper and lower setpoints

## Supported HVAC Modes

- **Off**: Turn the unit off
- **Cool**: Cooling mode
- **Heat**: Heating mode (if supported by device)
- **Dry**: Dehumidification mode (if supported by device)
- **Fan Only**: Fan only mode (if supported by device)
- **Auto**: Automatic heating/cooling mode (if supported by device)

## Supported Features

- **Temperature Control**: Set target temperature with 0.5°C precision
- **Fan Speed Control**: Auto, Low, Medium, High (depending on device capabilities)
- **Air Direction Control**: Horizontal, Vertical, Swing (if supported by device)
- **Current Temperature**: Monitor room temperature
- **Humidity Monitoring**: View current humidity levels

## API Information

This integration uses the unofficial Kumo Cloud API v3. The API endpoints and schemas were reverse-engineered from the mobile app and may change without notice.

## Troubleshooting

- **Authentication Errors**: Verify your Kumo Cloud credentials are correct
- **Connection Issues**: Check your internet connection and ensure the Kumo Cloud service is available
- **Device Not Responding**: Check that your climate control system is connected to Wi-Fi and online in the Kumo Cloud app

## Technical Details

- **Update Interval**: 60 seconds (configurable)
- **Token Refresh**: Automatic every 20 minutes
- **Temperature Unit**: Celsius (as used by the Kumo Cloud API)
- **API Base URL**: https://app-prod.kumocloud.com
- **API Version**: v3

## Support

If you encounter any issues, please check the [issue tracker](https://github.com/jjustinwilson/comfort_HA/issues) or create a new issue with details about your problem.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License. 