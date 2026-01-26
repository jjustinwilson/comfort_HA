"""The Kumo Cloud integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .const import CONF_SITE_ID, DOMAIN
from .coordinator import KumoCloudDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kumo Cloud from a config entry."""

    # Create API client
    api = KumoCloudAPI(hass)

    # Initialize with stored tokens if available
    if "access_token" in entry.data:
        api.username = entry.data[CONF_USERNAME]
        api.access_token = entry.data["access_token"]
        api.refresh_token = entry.data["refresh_token"]

    try:
        # Try to login or refresh tokens
        if not api.access_token:
            await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        else:
            # Verify the token works by making a test request
            try:
                await api.get_account_info()
            except KumoCloudAuthError:
                # Token expired, try to login again
                await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    except KumoCloudAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except KumoCloudConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to connect: {err}") from err

    # Create the coordinator
    coordinator = KumoCloudDataUpdateCoordinator(hass, api, entry.data[CONF_SITE_ID])

    # Fetch initial data so we have data when entities are added
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator in hass data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
