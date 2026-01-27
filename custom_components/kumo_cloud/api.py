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
        # Create dedicated session with appropriate timeouts for Kumo Cloud API
        # The API frequently takes longer than 10 seconds to respond
        self.timeout = ClientTimeout(total=45, connect=15, sock_read=30)
        self.session: aiohttp.ClientSession | None = None
        self.base_url = API_BASE_URL
        self.username: str | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: datetime | None = None
        # Rate limiting: ensure at least 2 seconds between requests
        # Only applies if a request was made recently to prevent 429 errors
        # The 60-second scan interval ensures we don't exceed API limits during normal operation
        self._last_request_time: datetime | None = None
        self._request_lock = asyncio.Lock()
        self._min_request_interval = timedelta(seconds=2)

    async def _ensure_session(self) -> None:
        """Ensure the session is created."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        """Close the session."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Login to Kumo Cloud and return user data."""
        await self._ensure_session()
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
            async with self.session.post(url, headers=headers, json=data) as response:
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

        await self._ensure_session()
        url = f"{self.base_url}/{API_VERSION}/refresh"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Content-Type": "application/json",
        }
        data = {"refresh": self.refresh_token}

        max_retries = 3
        retry_delay = 60  # Start with 60 seconds for 429 errors

        for attempt in range(max_retries):
            try:
                async with self.session.post(url, headers=headers, json=data) as response:
                    if response.status == 401:
                        raise KumoCloudAuthError("Refresh token expired")
                    if response.status == 429:
                        # Handle rate limiting with retry
                        if attempt < max_retries - 1:
                            _LOGGER.warning(
                                "Rate limited (429) during token refresh. Waiting %d seconds before retry %d/%d",
                                retry_delay,
                                attempt + 1,
                                max_retries,
                            )
                            try:
                                await asyncio.sleep(retry_delay)
                            except asyncio.CancelledError:
                                raise
                            retry_delay *= 2  # Exponential backoff
                            continue
                        else:
                            raise KumoCloudConnectionError(
                                "Rate limit exceeded during token refresh. Please try again later."
                            )
                    response.raise_for_status()
                    result = await response.json()

                    self.access_token = result["access"]
                    self.refresh_token = result["refresh"]
                    self.token_expires_at = datetime.now() + timedelta(
                        seconds=TOKEN_REFRESH_INTERVAL
                    )
                    return  # Success, exit retry loop

            except asyncio.TimeoutError as err:
                if attempt < max_retries - 1:
                    _LOGGER.warning(
                        "Connection timeout during token refresh. Retrying %d/%d",
                        attempt + 1,
                        max_retries,
                    )
                    continue
                raise KumoCloudConnectionError("Connection timeout during refresh") from err
            except ClientResponseError as err:
                if err.status == 401:
                    raise KumoCloudAuthError("Refresh token expired") from err
                if err.status == 429:
                    # This shouldn't happen as we handle it above, but just in case
                    if attempt < max_retries - 1:
                        _LOGGER.warning(
                            "Rate limited (429) during token refresh. Waiting %d seconds before retry %d/%d",
                            retry_delay,
                            attempt + 1,
                            max_retries,
                        )
                        try:
                            await asyncio.sleep(retry_delay)
                        except asyncio.CancelledError:
                            raise
                        retry_delay *= 2
                        continue
                    raise KumoCloudConnectionError(
                        "Rate limit exceeded during token refresh. Please try again later."
                    ) from err
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
        """Make an authenticated request to the API with rate limiting."""
        # Use lock to ensure only one request at a time
        async with self._request_lock:
            # Rate limiting: only wait if a request was made very recently
            # This prevents 429 errors while allowing rapid requests during initial setup
            if self._last_request_time is not None:
                time_since_last = datetime.now() - self._last_request_time
                if time_since_last < self._min_request_interval:
                    wait_time = (
                        self._min_request_interval - time_since_last
                    ).total_seconds()
                    # Only wait if it's a very short wait (less than 5 seconds)
                    # This prevents excessive delays during setup
                    if wait_time > 0:
                        _LOGGER.debug(
                            "Rate limiting: waiting %.1f seconds before next request",
                            wait_time,
                        )
                        try:
                            await asyncio.sleep(wait_time)
                        except asyncio.CancelledError:
                            # Re-raise cancellation to allow proper cleanup
                            raise

            await self._ensure_token_valid()
            await self._ensure_session()

            url = f"{self.base_url}/{API_VERSION}{endpoint}"
            headers = {
                "x-app-version": API_APP_VERSION,
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            max_retries = 3
            retry_delay = 60  # Start with 60 seconds for 429 errors

            for attempt in range(max_retries):
                got_429 = False
                try:
                    # Use ClientTimeout configured in session instead of asyncio.timeout
                    # This allows the API to take longer than 10 seconds without hard-killing requests
                    if method.upper() == "GET":
                        async with self.session.get(url, headers=headers) as response:
                            if response.status == 429:
                                got_429 = True
                            else:
                                response.raise_for_status()
                                result = await response.json()
                                self._last_request_time = datetime.now()
                                return result
                    elif method.upper() == "POST":
                        async with self.session.post(
                            url, headers=headers, json=data
                        ) as response:
                            if response.status == 429:
                                got_429 = True
                            else:
                                response.raise_for_status()
                                result = (
                                    await response.json()
                                    if response.content_type == "application/json"
                                    else {}
                                )
                                self._last_request_time = datetime.now()
                                return result

                    # Handle 429 errors with exponential backoff
                    if got_429:
                        if attempt < max_retries - 1:
                            _LOGGER.warning(
                                "Rate limited (429). Waiting %d seconds before retry %d/%d",
                                retry_delay,
                                attempt + 1,
                                max_retries,
                            )
                            try:
                                await asyncio.sleep(retry_delay)
                            except asyncio.CancelledError:
                                raise
                            retry_delay *= 2  # Exponential backoff
                            continue
                        else:
                            raise KumoCloudConnectionError(
                                "Rate limit exceeded. Please try again later."
                            )

                except asyncio.TimeoutError as err:
                    if attempt < max_retries - 1:
                        _LOGGER.warning(
                            "Request timeout. Retrying %d/%d", attempt + 1, max_retries
                        )
                        continue
                    raise KumoCloudConnectionError("Request timeout") from err
                except ClientResponseError as err:
                    if err.status == 401:
                        raise KumoCloudAuthError("Authentication failed") from err
                    if err.status == 429:
                        # This shouldn't happen as we handle it above, but just in case
                        if attempt < max_retries - 1:
                            _LOGGER.warning(
                                "Rate limited (429). Waiting %d seconds before retry %d/%d",
                                retry_delay,
                                attempt + 1,
                                max_retries,
                            )
                            try:
                                await asyncio.sleep(retry_delay)
                            except asyncio.CancelledError:
                                raise
                            retry_delay *= 2
                            continue
                        raise KumoCloudConnectionError(
                            "Rate limit exceeded. Please try again later."
                        ) from err
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
