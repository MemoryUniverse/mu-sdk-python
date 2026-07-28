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


# ---- consolidate / ask / tier-scoped recall (net-new this phase) ------------------------------


async def test_consolidate_extracts_facts_and_reports_real_counts(
    conformance_base_url: str,
) -> None:
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("Ada uses FalkorDB", subject="Ada", predicate="uses", object="FalkorDB")
        await client.add("Bob uses Postgres", subject="Bob", predicate="uses", object="Postgres")

        report = await client.consolidate()

        assert report.facts_extracted == 2
        assert report.added == 2
        assert report.superseded == 0


async def test_consolidate_supersedes_a_conflicting_fact_invalidate_dont_delete(
    conformance_base_url: str,
) -> None:
    """The MemGC/Phi headline scenario: a later fact sharing (subject, predicate) with a DIFFERENT
    object SUPERSEDES the prior one — proven end-to-end over the real wire (two `add` + two
    `consolidate` round trips), never a silent overwrite (the real demo server proves the same
    behaviour with genuine invalidate-don't-delete bi-temporal SPO; see
    `verify_supersession.py`)."""
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("Ada uses FalkorDB", subject="Ada", predicate="uses", object="FalkorDB")
        first_report = await client.consolidate()
        assert first_report.added == 1
        assert first_report.superseded == 0

        await client.add("Ada switched to Neo4j", subject="Ada", predicate="uses", object="Neo4j")
        second_report = await client.consolidate()
        assert second_report.facts_extracted == 1
        assert second_report.added == 1
        assert second_report.superseded == 1, "the FalkorDB fact was not reported as superseded"


async def test_ask_synthesizes_an_answer_from_the_current_consolidated_fact(
    conformance_base_url: str,
) -> None:
    """`ask()` answers from the CURRENT (post-supersession) fact only — contrast with `recall()`,
    which would still surface both raw memories."""
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("Ada uses FalkorDB", subject="Ada", predicate="uses", object="FalkorDB")
        await client.consolidate()
        await client.add("Ada switched to Neo4j", subject="Ada", predicate="uses", object="Neo4j")
        await client.consolidate()

        result = await client.ask("What does Ada use?")

        assert "Neo4j" in result.answer
        assert "FalkorDB" not in result.answer
        assert result.question == "What does Ada use?"


async def test_recall_tier_query_param_narrows_to_one_channel(
    conformance_base_url: str,
) -> None:
    """`tier=` (net-new arg) always wins over the default `channels` triple: only the matching
    tier's item comes back, and `channels_run` reports exactly that one channel."""
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("widget in stm", tier="stm")
        await client.add("widget in ltm", tier="ltm")

        stm_only = await client.recall("widget", tier="stm")
        assert len(stm_only.items) == 1
        assert stm_only.items[0].tier == "stm"
        assert stm_only.channels_run.stm is True
        assert stm_only.channels_run.mtm is False
        assert stm_only.channels_run.ltm is False

        ltm_only = await client.recall("widget", tier="ltm")
        assert len(ltm_only.items) == 1
        assert ltm_only.items[0].tier == "ltm"


async def test_recall_without_tier_arg_is_unchanged(conformance_base_url: str) -> None:
    """Backward-compat guard: omitting `tier=` (the default, `None`) behaves exactly as before —
    no behaviour change for an existing caller."""
    async with MemoryClient(settings=_settings(conformance_base_url)) as client:
        await client.add("widget in stm", tier="stm")
        await client.add("widget in ltm", tier="ltm")

        result = await client.recall("widget")
        assert len(result.items) == 2
