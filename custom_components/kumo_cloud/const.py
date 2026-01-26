"""Constants for the Kumo Cloud integration."""

DOMAIN = "kumo_cloud"

# Configuration constants
CONF_SITE_ID = "site_id"

# API constants
API_BASE_URL = "https://app-prod.kumocloud.com"
API_VERSION = "v3"
API_APP_VERSION = "3.0.9"

# Token refresh constants
TOKEN_REFRESH_INTERVAL = 1200  # 20 minutes in seconds
TOKEN_EXPIRY_MARGIN = 300  # 5 minutes margin in seconds

# Device constants
DEVICE_SERIAL = "deviceSerial"
ZONE_ID = "zoneId"
SITE_ID = "siteId"

# Operation modes
OPERATION_MODE_OFF = "off"
OPERATION_MODE_COOL = "cool"
OPERATION_MODE_HEAT = "heat"
OPERATION_MODE_DRY = "dry"
OPERATION_MODE_VENT = "vent"
OPERATION_MODE_AUTO = "auto"
OPERATION_MODE_AUTO_COOL = "autoCool"
OPERATION_MODE_AUTO_HEAT = "autoHeat"

# Fan speeds - API values from Mitsubishi
FAN_SPEED_AUTO = "auto"
FAN_SPEED_SUPER_QUIET = "superQuiet"
FAN_SPEED_QUIET = "quiet"
FAN_SPEED_LOW = "low"
FAN_SPEED_POWERFUL = "powerful"
FAN_SPEED_SUPER_POWERFUL = "superPowerful"

# Fan speed translation: API <-> UI
# Maps vendor-specific API strings to user-friendly UI labels
API_TO_UI_FAN = {
    "auto": "Auto",
    "superQuiet": "1 - Quiet",
    "quiet": "2 - Low",
    "low": "3 - Medium",
    "powerful": "4 - High",
    "superPowerful": "5 - Powerful",
}

UI_TO_API_FAN = {
    "Auto": "auto",
    "1 - Quiet": "superQuiet",
    "2 - Low": "quiet",
    "3 - Medium": "low",
    "4 - High": "powerful",
    "5 - Powerful": "superPowerful",
}

# All fan speeds in capability order (UI labels)
# Index 0 = auto, then speeds 1-5 based on numberOfFanSpeeds
UI_FAN_SPEEDS = ["Auto", "1 - Quiet", "2 - Low", "3 - Medium", "4 - High", "5 - Powerful"]

# Air direction - API values from Mitsubishi
AIR_DIRECTION_AUTO = "auto"
AIR_DIRECTION_SWING = "swing"
AIR_DIRECTION_HORIZONTAL = "horizontal"
AIR_DIRECTION_MID_HORIZONTAL = "midhorizontal"
AIR_DIRECTION_MIDPOINT = "midpoint"
AIR_DIRECTION_MID_VERTICAL = "midvertical"
AIR_DIRECTION_VERTICAL = "vertical"

# Vane (air direction) translation: API <-> UI
# Maps vendor-specific API strings to user-friendly UI labels
API_TO_UI_VANE = {
    "auto": "Auto",
    "swing": "Swing",
    "vertical": "1 - Lowest",
    "midvertical": "2 - Low",
    "midpoint": "3 - Middle",
    "midhorizontal": "4 - High",
    "horizontal": "5 - Highest",
}

UI_TO_API_VANE = {
    "Auto": "auto",
    "Swing": "swing",
    "1 - Lowest": "vertical",
    "2 - Low": "midvertical",
    "3 - Middle": "midpoint",
    "4 - High": "midhorizontal",
    "5 - Highest": "horizontal",
}

# All air directions in order (UI labels)
UI_VANE_POSITIONS = ["Auto", "Swing", "1 - Lowest", "2 - Low", "3 - Middle", "4 - High", "5 - Highest"]

# Default scan interval in seconds
DEFAULT_SCAN_INTERVAL = 60
