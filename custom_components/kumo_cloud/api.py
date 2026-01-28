"""API client for Kumo Cloud."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    API_BASE_URL,
    API_VERSION,
    API_APP_VERSION,
    TOKEN_REFRESH_INTERVAL,
    TOKEN_EXPIRY_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


class KumoCloudError(HomeAssistantError):
    """Base exception for Kumo Cloud."""


class KumoCloudAuthError(KumoCloudError):
    """Authentication error."""


class KumoCloudConnectionError(KumoCloudError):
    """Connection error."""


class KumoCloudAPI:
    """Kumo Cloud API client."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self.base_url = API_BASE_URL
        self.username: str | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: datetime | None = None

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Login to Kumo Cloud and return user data."""
        url = f"{self.base_url}/{API_VERSION}/login"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Content-Type": "application/json",
        }
        data = {
            "username": username,
            "password": password,
            "appVersion": API_APP_VERSION,
        }

        try:
            async with asyncio.timeout(30):
                async with self.session.post(
                    url, headers=headers, json=data
                ) as response:
                    if response.status == 403:
                        raise KumoCloudAuthError("Invalid username or password")
                    response.raise_for_status()
                    result = await response.json()

                    self.username = username
                    self.access_token = result["token"]["access"]
                    self.refresh_token = result["token"]["refresh"]
                    self.token_expires_at = datetime.now() + timedelta(
                        seconds=TOKEN_REFRESH_INTERVAL
                    )

                    return result

        except asyncio.TimeoutError as err:
            raise KumoCloudConnectionError("Connection timeout") from err
        except ClientResponseError as err:
            if err.status == 403:
                raise KumoCloudAuthError("Invalid credentials") from err
            raise KumoCloudConnectionError(f"HTTP error: {err.status}") from err
        except Exception as err:
            raise KumoCloudConnectionError(f"Unexpected error: {err}") from err

    async def refresh_access_token(self) -> None:
        """Refresh the access token."""
        if not self.refresh_token:
            raise KumoCloudAuthError("No refresh token available")

        url = f"{self.base_url}/{API_VERSION}/refresh"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Content-Type": "application/json",
        }
        data = {"refresh": self.refresh_token}

        try:
            async with asyncio.timeout(30):
                async with self.session.post(
                    url, headers=headers, json=data
                ) as response:
                    if response.status == 401:
                        raise KumoCloudAuthError("Refresh token expired")
                    response.raise_for_status()
                    result = await response.json()

                    self.access_token = result["access"]
                    self.refresh_token = result["refresh"]
                    self.token_expires_at = datetime.now() + timedelta(
                        seconds=TOKEN_REFRESH_INTERVAL
                    )

        except asyncio.TimeoutError as err:
            raise KumoCloudConnectionError("Connection timeout during refresh") from err
        except ClientResponseError as err:
            if err.status == 401:
                raise KumoCloudAuthError("Refresh token expired") from err
            raise KumoCloudConnectionError(
                f"HTTP error during refresh: {err.status}"
            ) from err

    async def _ensure_token_valid(self) -> None:
        """Ensure access token is valid, refresh if needed."""
        if not self.access_token:
            raise KumoCloudAuthError("No access token available")

        if (
            self.token_expires_at
            and datetime.now() + timedelta(seconds=TOKEN_EXPIRY_MARGIN)
            >= self.token_expires_at
        ):
            await self.refresh_access_token()

    async def _request(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make an authenticated request to the API."""
        await self._ensure_token_valid()

        url = f"{self.base_url}/{API_VERSION}{endpoint}"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with asyncio.timeout(30):
                if method.upper() == "GET":
                    async with self.session.get(url, headers=headers) as response:
                        response.raise_for_status()
                        return await response.json()
                elif method.upper() == "POST":
                    async with self.session.post(
                        url, headers=headers, json=data
                    ) as response:
                        response.raise_for_status()
                        if response.content_type == "application/json":
                            return await response.json()
                        return {}

        except asyncio.TimeoutError as err:
            raise KumoCloudConnectionError("Request timeout") from err
        except ClientResponseError as err:
            if err.status == 401:
                raise KumoCloudAuthError("Authentication failed") from err
            raise KumoCloudConnectionError(f"HTTP error: {err.status}") from err

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information."""
        return await self._request("GET", "/accounts/me")

    async def get_sites(self) -> list[dict[str, Any]]:
        """Get list of sites."""
        return await self._request("GET", "/sites/")

    async def get_zones(self, site_id: str) -> list[dict[str, Any]]:
        """Get list of zones for a site."""
        return await self._request("GET", f"/sites/{site_id}/zones")

    async def get_device_details(self, device_serial: str) -> dict[str, Any]:
        """Get device details."""
        return await self._request("GET", f"/devices/{device_serial}")

    async def get_device_profile(self, device_serial: str) -> list[dict[str, Any]]:
        """Get device profile information."""
        return await self._request("GET", f"/devices/{device_serial}/profile")

    async def send_command(
        self, device_serial: str, commands: dict[str, Any]
    ) -> dict[str, Any]:
        """Send command to device."""
        data = {"deviceSerial": device_serial, "commands": commands}
        return await self._request("POST", "/devices/send-command", data)
