"""Tests for Kumo Cloud coordinator refresh behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kumo_cloud.api import KumoCloudAuthError, KumoCloudConnectionError
from custom_components.kumo_cloud.coordinator import KumoCloudDataUpdateCoordinator
from custom_components.kumo_cloud.const import CONF_SITE_ID, DOMAIN


@pytest.fixture
def api() -> AsyncMock:
    """Return a mocked Kumo Cloud API client."""
    client = AsyncMock()
    client.get_zones = AsyncMock(
        return_value=[
            {
                "id": "zone-1",
                "name": "Zone A",
                "adapter": {
                    "deviceSerial": "device-1",
                    "hasSensor": True,
                },
            },
            {
                "id": "zone-2",
                "name": "Zone B",
                "adapter": {
                    "deviceSerial": "device-2",
                    "hasSensor": False,
                },
            },
        ]
    )
    client.get_device_details = AsyncMock(
        side_effect=[
            {"serialNumber": "device-1", "updatedAt": "2026-01-01T00:00:00+00:00"},
            {"serialNumber": "device-2", "updatedAt": "2026-01-01T00:00:00+00:00"},
        ]
    )
    client.get_device_profile = AsyncMock(side_effect=[[{"hasModeHeat": True}], []])
    client.get_device_status = AsyncMock(
        side_effect=[{"firmwareVersion": "1.0.0"}, {"firmwareVersion": "1.0.1"}]
    )
    client.get_zone_notification_preferences = AsyncMock(
        side_effect=[{"filterDirtyReminderInterval": 90}, {}]
    )
    client.get_wireless_sensor = AsyncMock(
        return_value={"battery": 90, "rssi": -55, "temperature": 21.5}
    )
    client.refresh_access_token = AsyncMock()
    client.access_token = "access-token"
    client.refresh_token = "refresh-token"
    return client


@pytest.fixture
def coordinator(hass, api: AsyncMock) -> KumoCloudDataUpdateCoordinator:
    """Return a coordinator using a mocked API client."""
    return KumoCloudDataUpdateCoordinator(hass, api, "site-1")


async def test_rejected_refreshed_token_starts_reauth_without_another_retry(
    coordinator: KumoCloudDataUpdateCoordinator, api: AsyncMock,
) -> None:
    """A second auth rejection stops rather than recursively refreshing again."""
    api.get_zones.side_effect = [
        KumoCloudAuthError("expired"),
        KumoCloudAuthError("still rejected"),
        RuntimeError("unexpected third request"),
    ]

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    api.refresh_access_token.assert_awaited_once()
    assert api.get_zones.await_count == 2


async def test_refresh_connection_failure_is_retryable(
    coordinator: KumoCloudDataUpdateCoordinator, api: AsyncMock,
) -> None:
    """A temporary refresh failure does not require new credentials."""
    api.get_zones.side_effect = KumoCloudAuthError("expired")
    api.refresh_access_token.side_effect = KumoCloudConnectionError("unreachable")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    api.refresh_access_token.assert_awaited_once()


async def test_refresh_assembles_zone_device_profile_and_status_data(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """Zones with adapters create device, profile, status, and sensor data."""
    data = await coordinator._async_update_data()

    assert [zone["id"] for zone in data["zones"]] == ["zone-1", "zone-2"]
    assert set(data["devices"]) == {"device-1", "device-2"}
    assert data["device_profiles"]["device-1"] == [{"hasModeHeat": True}]
    assert data["device_statuses"]["device-1"] == {"firmwareVersion": "1.0.0"}
    assert data["wireless_sensors"]["device-1"] == {
        "battery": 90,
        "rssi": -55,
        "temperature": 21.5,
    }
    assert "device-2" not in data["wireless_sensors"]
    api.get_wireless_sensor.assert_awaited_once_with("device-1")


async def test_optional_endpoint_failures_do_not_fail_refresh(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """Optional endpoint failures are omitted without failing the whole refresh."""
    api.get_device_status.side_effect = KumoCloudConnectionError("status failed")
    api.get_zone_notification_preferences.side_effect = KumoCloudConnectionError(
        "notifications failed"
    )

    data = await coordinator._async_update_data()

    assert set(data["devices"]) == {"device-1", "device-2"}
    assert data["device_statuses"] == {}
    assert data["zone_notifications"] == {}


async def test_auth_failure_refreshes_token_once_then_retries(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """An auth failure refreshes the token once and retries the refresh."""
    retry_zones = [
        {
            "id": "zone-1",
            "name": "Zone A",
            "adapter": {"deviceSerial": "device-1", "hasSensor": False},
        }
    ]
    api.get_zones.side_effect = [KumoCloudAuthError("expired"), retry_zones]
    api.get_device_details.side_effect = [
        {"serialNumber": "device-1", "updatedAt": "2026-01-01T00:00:00+00:00"}
    ]
    api.get_device_profile.side_effect = [[]]
    api.get_device_status.side_effect = [{}]
    api.get_zone_notification_preferences.side_effect = [{}]

    data = await coordinator._async_update_data()

    api.refresh_access_token.assert_awaited_once()
    assert list(data["devices"]) == ["device-1"]


async def test_auth_failure_in_device_request_refreshes_token_once_then_retries(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """An auth failure from parallel per-device requests refreshes and retries."""
    retry_zones = [
        {
            "id": "zone-1",
            "name": "Zone A",
            "adapter": {"deviceSerial": "device-1", "hasSensor": False},
        }
    ]
    api.get_zones.side_effect = [
        retry_zones,
        retry_zones,
    ]
    api.get_device_details.side_effect = [
        KumoCloudAuthError("expired"),
        {"serialNumber": "device-1", "updatedAt": "2026-01-01T00:00:00+00:00"},
    ]
    api.get_device_profile.side_effect = [[{"hasModeHeat": True}], []]
    api.get_device_status.side_effect = [{"firmwareVersion": "stale"}, {}]
    api.get_zone_notification_preferences.side_effect = [
        {"filterDirtyReminderInterval": 90},
        {},
    ]

    data = await coordinator._async_update_data()

    api.refresh_access_token.assert_awaited_once()
    assert data["devices"]["device-1"]["serialNumber"] == "device-1"


async def test_runtime_token_refresh_persists_rotated_tokens(
    hass,
    api: AsyncMock,
) -> None:
    """Runtime refresh saves rotated tokens so restarts keep the new refresh token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kumo Cloud - Home",
        data={
            CONF_USERNAME: "user@example.invalid",
            CONF_SITE_ID: "site-1",
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
        },
    )
    entry.add_to_hass(hass)
    coordinator = KumoCloudDataUpdateCoordinator(hass, api, "site-1", entry)
    retry_zones = [
        {
            "id": "zone-1",
            "name": "Zone A",
            "adapter": {"deviceSerial": "device-1", "hasSensor": False},
        }
    ]
    api.get_zones.side_effect = [KumoCloudAuthError("expired"), retry_zones]

    async def rotate_tokens() -> None:
        api.access_token = "new-access-token"
        api.refresh_token = "new-refresh-token"

    api.refresh_access_token.side_effect = rotate_tokens
    api.get_device_details.side_effect = [
        {"serialNumber": "device-1", "updatedAt": "2026-01-01T00:00:00+00:00"}
    ]
    api.get_device_profile.side_effect = [[]]
    api.get_device_status.side_effect = [{}]
    api.get_zone_notification_preferences.side_effect = [{}]

    await coordinator._async_update_data()

    assert entry.data == {
        CONF_USERNAME: "user@example.invalid",
        CONF_SITE_ID: "site-1",
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
    }


async def test_proactive_api_token_refresh_persists_rotated_tokens(
    hass,
    api: AsyncMock,
) -> None:
    """Tokens refreshed inside normal API requests are saved for restart."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kumo Cloud - Home",
        data={
            CONF_USERNAME: "user@example.invalid",
            CONF_SITE_ID: "site-1",
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
        },
    )
    entry.add_to_hass(hass)
    coordinator = KumoCloudDataUpdateCoordinator(hass, api, "site-1", entry)

    async def get_zones_with_rotated_tokens(
        site_id: str,
    ) -> list[dict[str, object]]:
        assert site_id == "site-1"
        api.access_token = "new-access-token"
        api.refresh_token = "new-refresh-token"
        return [
            {
                "id": "zone-1",
                "name": "Zone A",
                "adapter": {"deviceSerial": "device-1", "hasSensor": False},
            }
        ]

    api.get_zones.side_effect = get_zones_with_rotated_tokens
    api.get_device_details.side_effect = [
        {"serialNumber": "device-1", "updatedAt": "2026-01-01T00:00:00+00:00"}
    ]
    api.get_device_profile.side_effect = [[]]
    api.get_device_status.side_effect = [{}]
    api.get_zone_notification_preferences.side_effect = [{}]

    await coordinator._async_update_data()

    assert entry.data == {
        CONF_USERNAME: "user@example.invalid",
        CONF_SITE_ID: "site-1",
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
    }


async def test_expired_runtime_refresh_token_starts_reauth(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """If refresh fails with auth, Home Assistant should start reauthentication."""
    api.get_zones.side_effect = KumoCloudAuthError("expired access token")
    api.refresh_access_token.side_effect = KumoCloudAuthError("expired refresh token")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_persistent_connection_failure_raises_update_failed(
    coordinator: KumoCloudDataUpdateCoordinator,
    api: AsyncMock,
) -> None:
    """Persistent API connection failures become Home Assistant UpdateFailed."""
    api.get_zones.side_effect = KumoCloudConnectionError("offline")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
