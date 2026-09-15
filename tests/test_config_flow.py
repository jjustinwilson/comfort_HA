"""Tests for the Kumo Cloud config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, start_reauth_flow

from custom_components.kumo_cloud.api import KumoCloudAuthError, KumoCloudConnectionError
from custom_components.kumo_cloud.const import CONF_SITE_ID, DOMAIN


def _auth_info(
    *,
    sites: list[dict[str, str]] | None = None,
    access_token: str = "new-access-token",
    refresh_token: str = "new-refresh-token",
) -> dict[str, object]:
    """Return synthetic successful auth validation info."""
    api = AsyncMock()
    api.access_token = access_token
    api.refresh_token = refresh_token
    return {
        "login_result": {},
        "account_info": {"id": "account-1"},
        "sites": sites or [{"id": "site-1", "name": "Home"}],
        "api": api,
    }


@pytest.fixture
def mock_setup_entry(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Mock config entry setup so config-flow tests stay offline."""
    setup_entry = AsyncMock(return_value=True)
    monkeypatch.setattr("custom_components.kumo_cloud.async_setup_entry", setup_entry)
    return setup_entry


async def test_invalid_credentials_return_invalid_auth(
    hass,
    enable_custom_integrations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid credentials keep the user on the form with invalid_auth."""
    validate_auth = AsyncMock(side_effect=KumoCloudAuthError("invalid"))
    monkeypatch.setattr("custom_components.kumo_cloud.config_flow.validate_auth", validate_auth)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "user@example.invalid", CONF_PASSWORD: "bad-password"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_connection_failure_returns_cannot_connect(
    hass,
    enable_custom_integrations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failures keep the user on the form with cannot_connect."""
    validate_auth = AsyncMock(side_effect=KumoCloudConnectionError("timeout"))
    monkeypatch.setattr("custom_components.kumo_cloud.config_flow.validate_auth", validate_auth)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "user@example.invalid", CONF_PASSWORD: "password"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_single_site_account_auto_creates_entry_without_password(
    hass,
    enable_custom_integrations,
    mock_setup_entry: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-site account creates an entry and stores only token data."""
    validate_auth = AsyncMock(return_value=_auth_info())
    monkeypatch.setattr("custom_components.kumo_cloud.config_flow.validate_auth", validate_auth)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "user@example.invalid", CONF_PASSWORD: "password"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kumo Cloud - Home"
    assert result["data"] == {
        CONF_USERNAME: "user@example.invalid",
        CONF_SITE_ID: "site-1",
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
    }
    assert CONF_PASSWORD not in result["data"]


async def test_multi_site_account_prompts_for_site_selection(
    hass,
    enable_custom_integrations,
    mock_setup_entry: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-site account asks which site should be configured."""
    validate_auth = AsyncMock(
        return_value=_auth_info(
            sites=[
                {"id": "site-1", "name": "Home"},
                {"id": "site-2", "name": "Cabin"},
            ]
        )
    )
    monkeypatch.setattr("custom_components.kumo_cloud.config_flow.validate_auth", validate_auth)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "user@example.invalid", CONF_PASSWORD: "password"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "site"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SITE_ID: "site-2"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kumo Cloud - Cabin"
    assert result["data"][CONF_SITE_ID] == "site-2"
    assert CONF_PASSWORD not in result["data"]


async def test_reauth_updates_tokens_and_removes_legacy_password(
    hass,
    enable_custom_integrations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reauth persists fresh tokens and removes legacy password storage."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kumo Cloud - Home",
        unique_id="user@example.invalid",
        data={
            CONF_USERNAME: "user@example.invalid",
            CONF_PASSWORD: "old-password",
            CONF_SITE_ID: "site-1",
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
        },
    )
    entry.add_to_hass(hass)
    validate_auth = AsyncMock(
        return_value=_auth_info(
            access_token="reauth-access-token",
            refresh_token="reauth-refresh-token",
        )
    )
    monkeypatch.setattr("custom_components.kumo_cloud.config_flow.validate_auth", validate_auth)
    reload_entry = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)

    result = await start_reauth_flow(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "new-password"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {
        CONF_USERNAME: "user@example.invalid",
        CONF_SITE_ID: "site-1",
        "access_token": "reauth-access-token",
        "refresh_token": "reauth-refresh-token",
    }
    validate_auth.assert_awaited_once_with(
        hass,
        {CONF_USERNAME: "user@example.invalid", CONF_PASSWORD: "new-password"},
    )
    reload_entry.assert_awaited_once_with(entry.entry_id)
