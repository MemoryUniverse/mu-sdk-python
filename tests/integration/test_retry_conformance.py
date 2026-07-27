"""Integration proof for FIX-1: a 429/503 response must actually trigger the SDK's real retry
decorator stack (`with_retry` wraps `MemoryClient._raw_request` directly, and `raise_for_wire_error`
now runs INSIDE that function — see `mu_sdk.client` module docstring). Before that fix,
`raise_for_wire_error` ran only after `_execute` returned, i.e. after retries were already
exhausted, so `with_retry`'s `retry_if_exception_type(TransientSdkError)` never saw a 429/503 at
all. Zero mocks: real `MemoryClient`, real `httpx` transport, real TCP against the conformance
server's `/test/flaky/{key}` route (`tests/conformance_server/app.py`), which fails the first
`fail_times` requests for a given key server-side before succeeding.
"""

from __future__ import annotations

import uuid

import pytest

from mu_sdk.client import MemoryClient
from mu_sdk.errors import RateLimitedError, ServiceUnavailableError
from mu_sdk.settings import SdkIdentity, SdkSettings

pytestmark = pytest.mark.integration


def _settings(base_url: str) -> SdkSettings:
    """Short backoff so the test doesn't sleep for real multi-second backoff durations —
    mirrors `test_memory_client_conformance.py`'s `_settings` helper. `/test/flaky/*` doesn't
    require auth, but `MemoryClient.__init__` still resolves an auth strategy eagerly (see
    `mu_sdk.auth.resolve_auth`), so a complete identity is needed just to construct the client."""
    return SdkSettings(
        base_url=base_url,
        identity=SdkIdentity(
            user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="session-1"
        ),
        max_retries=3,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
        timeout_s=5.0,
    )


async def _attempts_seen(client: MemoryClient, key: str) -> int:
    response = await client._execute("GET", f"/test/flaky/{key}/attempts")
    assert isinstance(response.json_body, dict)
    return int(response.json_body["attempts"])


async def test_503_then_200_is_retried_to_success(conformance_base_url: str) -> None:
    key = uuid.uuid4().hex
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        response = await client._execute(
            "GET",
            f"/test/flaky/{key}",
            params={"fail_times": 2, "status_code": 503},
        )
        assert response.status_code == 200
        assert response.json_body == {"attempts": 3}  # 2 failures + the succeeding attempt

        # the server-side counter is the ground truth that real retries actually fired, not just
        # that the call happened to succeed on the first try
        assert await _attempts_seen(client, key) == 3


async def test_429_then_200_is_retried_to_success(conformance_base_url: str) -> None:
    key = uuid.uuid4().hex
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        response = await client._execute(
            "GET",
            f"/test/flaky/{key}",
            params={"fail_times": 1, "status_code": 429},
        )
        assert response.status_code == 200
        assert await _attempts_seen(client, key) == 2


async def test_persistent_503_exhausts_retries_and_raises(conformance_base_url: str) -> None:
    """`max_retries=3` means 4 total attempts; a route that never recovers must still raise
    `ServiceUnavailableError` after those are exhausted, proving the retry loop has a ceiling."""
    key = uuid.uuid4().hex
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        with pytest.raises(ServiceUnavailableError):
            await client._execute(
                "GET",
                f"/test/flaky/{key}",
                params={"fail_times": 999, "status_code": 503},
            )
        assert await _attempts_seen(client, key) == 4  # 1 initial + 3 retries, then gave up


async def test_persistent_429_exhausts_retries_and_raises(conformance_base_url: str) -> None:
    key = uuid.uuid4().hex
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        with pytest.raises(RateLimitedError):
            await client._execute(
                "GET",
                f"/test/flaky/{key}",
                params={"fail_times": 999, "status_code": 429},
            )
        assert await _attempts_seen(client, key) == 4
