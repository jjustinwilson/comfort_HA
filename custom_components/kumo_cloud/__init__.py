"""The Kumo Cloud integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .coordinator import KumoCloudDataUpdateCoordinator
from .const import CONF_SITE_ID, DOMAIN

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
            if CONF_PASSWORD not in entry.data:
                raise KumoCloudAuthError("Reauthentication required")
            await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        else:
            # Verify the token works by making a test request
            try:
                await api.get_account_info()
            except KumoCloudAuthError:
                # Access token expired. Prefer the stored refresh token because
                # the account password is no longer persisted.
                try:
                    await api.refresh_access_token()
                    await api.get_account_info()
                except KumoCloudAuthError:
                    if CONF_PASSWORD not in entry.data:
                        raise
                    await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    except KumoCloudAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except KumoCloudConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to connect: {err}") from err

    # Remove legacy plaintext password storage and persist refreshed tokens.
    updated_data = dict(entry.data)
    needs_entry_update = False

    if CONF_PASSWORD in updated_data:
        updated_data.pop(CONF_PASSWORD, None)
        needs_entry_update = True

    if api.access_token and updated_data.get("access_token") != api.access_token:
        updated_data["access_token"] = api.access_token
        needs_entry_update = True

    if api.refresh_token and updated_data.get("refresh_token") != api.refresh_token:
        updated_data["refresh_token"] = api.refresh_token
        needs_entry_update = True

    if needs_entry_update:
        hass.config_entries.async_update_entry(entry, data=updated_data)

    # Create the coordinator
    coordinator = KumoCloudDataUpdateCoordinator(hass, api, entry.data[CONF_SITE_ID], entry)

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
