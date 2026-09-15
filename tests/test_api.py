"""Tests for the Kumo Cloud API boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import ClientResponseError

from custom_components.kumo_cloud.api import (
    KumoCloudAPI,
    KumoCloudAuthError,
    KumoCloudConnectionError,
)


class FakeResponse:
    """Minimal aiohttp response context manager for API tests."""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self._payload = payload or {}
        self.content_type = content_type

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise ClientResponseError(
                request_info=None,
                history=(),
                status=self.status,
                message="failed",
            )

    async def json(self) -> dict[str, Any] | list[dict[str, Any]]:
        return self._payload


class FakeSession:
    """Minimal session that returns queued responses for API tests."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self.post_responses: list[FakeResponse | BaseException] = []
        self.get_responses: list[FakeResponse | BaseException] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, kwargs))
        return self._next_response(self.post_responses)

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.gets.append(url)
        return self._next_response(self.get_responses)

    @staticmethod
    def _next_response(responses: list[FakeResponse | BaseException]) -> FakeResponse:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def session() -> FakeSession:
    """Return a fake aiohttp session."""
    return FakeSession()


@pytest.fixture
def api(session: FakeSession) -> KumoCloudAPI:
    """Return an API client with a fake session."""
    client = KumoCloudAPI.__new__(KumoCloudAPI)
    client.hass = None
    client.session = session
    client.base_url = "https://example.invalid"
    client.username = None
    client.access_token = None
    client.refresh_token = None
    client.token_expires_at = None
    return client


def _client_response_error(status: int) -> ClientResponseError:
    """Return a synthetic aiohttp response error."""
    return ClientResponseError(
        request_info=None,
        history=(),
        status=status,
        message="failed",
    )


async def test_login_stores_access_and_refresh_tokens(
    api: KumoCloudAPI,
    session: FakeSession,
) -> None:
    """Login stores the access and refresh tokens returned by Kumo Cloud."""
    session.post_responses.append(
        FakeResponse(
            payload={
                "token": {
                    "access": "access-token",
                    "refresh": "refresh-token",
                }
            }
        )
    )

    result = await api.login("user@example.invalid", "password")

    assert result["token"]["access"] == "access-token"
    assert api.username == "user@example.invalid"
    assert api.access_token == "access-token"
    assert api.refresh_token == "refresh-token"
    assert api.token_expires_at is not None


async def test_refresh_updates_tokens(api: KumoCloudAPI, session: FakeSession) -> None:
    """Token refresh replaces both stored tokens."""
    api.refresh_token = "old-refresh-token"
    session.post_responses.append(
        FakeResponse(payload={"access": "new-access-token", "refresh": "new-refresh-token"})
    )

    await api.refresh_access_token()

    assert api.access_token == "new-access-token"
    assert api.refresh_token == "new-refresh-token"
    assert api.token_expires_at is not None


@pytest.mark.parametrize("status", [401, 403])
async def test_request_auth_failures_raise_auth_error(
    api: KumoCloudAPI,
    session: FakeSession,
    status: int,
) -> None:
    """Authenticated request 401/403 responses map to auth errors."""
    api.access_token = "access-token"
    session.get_responses.append(FakeResponse(status=status))

    with pytest.raises(KumoCloudAuthError):
        await api.get_account_info()


async def test_rate_limit_retries_with_bounded_backoff(
    api: KumoCloudAPI,
    session: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 429 responses retry with bounded exponential backoff."""
    api.access_token = "access-token"
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.kumo_cloud.api.asyncio.sleep", sleep)
    session.get_responses.extend(
        [
            FakeResponse(status=429),
            FakeResponse(payload={"id": "account-1"}),
        ]
    )

    result = await api.get_account_info()

    assert result == {"id": "account-1"}
    sleep.assert_awaited_once_with(60)
    assert len(session.gets) == 2


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: aiohttp.ClientConnectionError("socket failure"),
        lambda: OSError("DNS failure"),
    ],
)
@pytest.mark.parametrize("operation", ["get", "post", "refresh"])
async def test_transport_failures_raise_connection_error(
    api: KumoCloudAPI,
    session: FakeSession,
    error_factory: Callable[[], BaseException],
    operation: str,
) -> None:
    """DNS, socket, and aiohttp transport failures map to connection errors."""
    api.access_token = "access-token"
    api.refresh_token = "refresh-token"
    if operation == "get":
        session.get_responses.append(error_factory())
        request = api.get_account_info()
    elif operation == "post":
        session.post_responses.append(error_factory())
        request = api.send_command("device-1", {"power": 0})
    else:
        session.post_responses.append(error_factory())
        request = api.refresh_access_token()

    with pytest.raises(KumoCloudConnectionError):
        await request


async def test_logs_do_not_include_raw_tokens_or_passwords(
    api: KumoCloudAPI,
    session: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry logging does not include raw token or password material."""
    api.access_token = "placeholder-access-token"
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.kumo_cloud.api.asyncio.sleep", sleep)
    session.get_responses.extend(
        [
            FakeResponse(status=429),
            FakeResponse(payload={"id": "account-1"}),
        ]
    )

    await api.get_account_info()

    assert "placeholder-access-token" not in caplog.text
    assert "placeholder-password" not in caplog.text
