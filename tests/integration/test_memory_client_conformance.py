"""Integration suite: the SDK's real `MemoryClient` (real `httpx.AsyncClient` via `HttpxTransport`)
against the real conformance server (`conformance_base_url` fixture) over real TCP. Asserts:
correct serialized request payloads / response parsing, error-code -> SDK-exception mapping, and
idempotent-replay/conflict behavior. ZERO mocks (DEV-STANDARDS).
"""

from __future__ import annotations

import pytest
from mu_contracts.domain.model.memory import Visibility

from mu_sdk.client import MemoryClient
from mu_sdk.errors import (
    AuthenticationError,
    ConflictError,
    PrivateDataRejectedError,
)
from mu_sdk.models.memory import MemoryResponse
from mu_sdk.settings import SdkIdentity, SdkSettings

pytestmark = pytest.mark.integration


def _settings(base_url: str, *, session_id: str = "session-1") -> SdkSettings:
    return SdkSettings(
        base_url=base_url,
        identity=SdkIdentity(
            user_id="alice",
            workspace_id="ws-1",
            namespace_id="ns-1",
            session_id=session_id,
        ),
        max_retries=1,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
        timeout_s=5.0,
    )


async def test_add_then_search_round_trips_on_the_real_wire(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        added = await client.add("the sky is blue", visibility=Visibility.SHARED)
        assert isinstance(added, MemoryResponse)
        assert added.content == "the sky is blue"
        assert added.id

        found = await client.search("sky")
        assert found.total == 1
        assert found.memories[0].id == added.id
        assert found.memories[0].content == "the sky is blue"


async def test_search_excludes_non_matching_content(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("apples are red")
        result = await client.search("banana")
        assert result.total == 0
        assert result.memories == []


async def test_add_private_visibility_is_rejected_by_the_shared_route(
    conformance_base_url: str,
) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        with pytest.raises(PrivateDataRejectedError) as exc_info:
            await client.add("a private thought", visibility=Visibility.PRIVATE)
        assert exc_info.value.status_code == 403
        assert exc_info.value.request_id is not None


async def test_idempotent_replay_returns_the_original_response(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        first = await client.add("idempotent fact", idempotency_key="key-1")
        second = await client.add("idempotent fact", idempotency_key="key-1")
        assert first.id == second.id


async def test_conflicting_idempotent_replay_raises_conflict(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("fact A", idempotency_key="key-2")
        with pytest.raises(ConflictError) as exc_info:
            await client.add("fact B (different body, same key)", idempotency_key="key-2")
        assert exc_info.value.status_code == 409


async def test_recall_returns_ranked_items_with_the_resolved_namespace(
    conformance_base_url: str,
) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("paris is the capital of france")
        await client.add("tokyo is the capital of japan")

        result = await client.recall("capital of france")

        assert len(result.items) == 1
        assert "paris" in result.items[0].content
        assert result.namespace.workspace == "ws-1"
        assert result.namespace.session == "session-1"
        assert result.memory_ids == [result.items[0].memory_id]


async def test_recall_respects_the_limit(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        for i in range(5):
            await client.add(f"note number {i} about oranges")
        result = await client.recall("oranges", limit=2)
        assert len(result.items) == 2


async def test_recall_with_no_explicit_limit_uses_the_settings_derived_default(
    conformance_base_url: str,
) -> None:
    """FIX-2: omitting `limit=` must resolve from `SdkSettings.default_recall_limit`, not a
    hardcoded literal — proven end-to-end by setting a non-default value and confirming the
    conformance server (which slices its results by the `limit` it receives on the wire) honors
    it."""
    settings = SdkSettings(
        base_url=conformance_base_url,
        identity=_settings(conformance_base_url).identity,
        default_recall_limit=3,
    )
    async with MemoryClient(settings=settings) as client:
        for i in range(5):
            await client.add(f"note number {i} about grapefruit")
        result = await client.recall("grapefruit")  # no limit= passed
        assert len(result.items) == 3


async def test_context_discover_round_trips(conformance_base_url: str) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        view = await client.context.discover("session-1")
        assert view.session_id == "session-1"
        assert view.indexes == []


async def test_tenancy_isolation_by_identity(conformance_base_url: str) -> None:
    """Two different identities never see each other's memories — even hitting the same
    conformance server instance (the same discipline the real tenancy guard enforces)."""
    async with MemoryClient(settings=_settings(conformance_base_url)) as alice_client:
        await alice_client.add("alice's secret note")

    bob_settings = SdkSettings(
        base_url=conformance_base_url,
        identity=SdkIdentity(
            user_id="bob", workspace_id="ws-1", namespace_id="ns-1", session_id="session-2"
        ),
    )
    async with MemoryClient(settings=bob_settings) as bob_client:
        result = await bob_client.search("secret")
        assert result.total == 0


async def test_missing_credentials_raise_authentication_error_from_the_real_server(
    conformance_base_url: str,
) -> None:
    """A request that reaches the real server with NO identity at all (empty demo auth headers)
    gets a real 401 back, mapped to `AuthenticationError` — exercised through the real transport
    by constructing the client with an auth stub that sends no headers."""
    from mu_sdk.auth import SdkAuth

    class _NoAuth:
        def headers(self) -> dict[str, str]:
            return {}

    auth: SdkAuth = _NoAuth()
    async with MemoryClient(
        settings=SdkSettings(base_url=conformance_base_url), auth=auth
    ) as client:
        with pytest.raises(AuthenticationError) as exc_info:
            await client.search("anything")
        assert exc_info.value.status_code == 401
