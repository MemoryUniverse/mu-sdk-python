"""Pure-unit: `MemoryClient`'s request CONSTRUCTION for the net-new verbs (`consolidate`/`ask`,
`recall(tier=...)`) against a fake `Transport` (records the call, returns a canned response) —
never a mock of `httpx` itself (DEV-STANDARDS: mocks ONLY in pure unit tests; the real wire
round-trip is covered by `tests/integration/test_memory_client_conformance.py` against the real
conformance server, zero mocks there)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from mu_contracts.contracts.views import ConsolidateView
from mu_contracts.domain.errors import PlaneFieldRejectedError
from mu_contracts.domain.model.memory import Visibility

from mu_sdk.client import MemoryClient
from mu_sdk.errors import SurfaceVerbNotImplementedError
from mu_sdk.models.consolidate import AskResult
from mu_sdk.models.context import ContextView
from mu_sdk.models.memory import MemoryResponse, MemoryWriteResult
from mu_sdk.settings import SdkIdentity, SdkSettings
from mu_sdk.transport import Transport, TransportResponse

pytestmark = pytest.mark.unit


@dataclass
class _RecordingTransport:
    """A minimal `Transport` fake: returns one canned `TransportResponse` per call and records
    every request it was asked to make, so a test can assert on exactly what `MemoryClient` sent
    over the wire without a real socket."""

    response: TransportResponse
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> TransportResponse:
        self.calls.append(
            {"method": method, "path": path, "params": params, "json_body": json_body}
        )
        return self.response

    async def aclose(self) -> None:
        return None


def _settings() -> SdkSettings:
    return SdkSettings(
        base_url="http://unit-test.invalid",
        identity=SdkIdentity(
            user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="s-1"
        ),
        max_retries=0,
    )


def _transport(json_body: dict[str, Any]) -> _RecordingTransport:
    return _RecordingTransport(response=TransportResponse(status_code=200, json_body=json_body))


async def test_consolidate_posts_to_the_net_new_route_with_the_limit_body() -> None:
    """R2: `consolidate()` now returns the canonical `ConsolidateView` (R0's "A4 winner") — this
    canned transport still returns the OLD legacy shape (`generated_at`, no `noop`), proving the
    fallback parse in `_parse_consolidate_response` remaps it transparently (`noop=0`, since that
    legacy shape never tracked NOOP actions)."""
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {"facts_extracted": 2, "added": 1, "superseded": 1, "generated_at": now}
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.consolidate(limit=25)

    assert isinstance(result, ConsolidateView)
    assert result.superseded == 1
    assert result.noop == 0
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["path"] == "/v1/memories/consolidate"
    assert recorded[0]["json_body"] == {"limit": 25}


async def test_ask_posts_to_the_net_new_route_and_returns_the_synthesized_answer() -> None:
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {"question": "Where does Ada work?", "answer": "Acme", "generated_at": now}
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.ask("Where does Ada work?")

    assert isinstance(result, AskResult)
    assert result.answer == "Acme"
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["path"] == "/v1/memories/ask"
    assert recorded[0]["json_body"]["question"] == "Where does Ada work?"


async def test_recall_with_tier_sends_it_as_a_query_param_not_a_body_field() -> None:
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {
            "namespace": {
                "org": "org-1",
                "workspace": "ws-1",
                "user": "*",  # SHARED namespace requires user="*" (CANONICAL §1 rule 4)
                "session": "s-1",
                "visibility": "shared",
            },
            "items": [],
            "channels_run": {"stm": True, "mtm": False, "ltm": False},
            "generated_at": now,
        }
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        await client.recall("find me", tier="stm")

    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["params"] == {"tier": "stm"}
    assert "tier" not in recorded[0]["json_body"]


async def test_recall_without_tier_sends_no_query_params() -> None:
    """Backward-compat: omitting `tier=` sends no query string at all — byte-for-byte the prior
    request shape (no behaviour change for an existing caller)."""
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {
            "namespace": {
                "org": "org-1",
                "workspace": "ws-1",
                "user": "*",  # SHARED namespace requires user="*" (CANONICAL §1 rule 4)
                "session": "s-1",
                "visibility": "shared",
            },
            "items": [],
            "channels_run": {"stm": True, "mtm": True, "ltm": True},
            "generated_at": now,
        }
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        await client.recall("find me")

    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["params"] is None


# ---- add() -> MemoryWriteResult receipt mapping (Decision B; net-new this phase) --------------


async def test_add_maps_the_frozen_wire_response_down_to_a_write_receipt() -> None:
    """The wire `POST /memories` body is still the frozen full-row `MemoryResponse` shape
    (Appendix A.1, unedited) — `add()` now maps it DOWN to a `MemoryWriteResult` receipt
    client-side (Decision B), deriving `promoted`/`tiers_written` from the returned `tier` since
    the wire body carries neither flag directly."""
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {
            "id": "mem-1",
            "content": "hello",
            "content_type": "text",
            "tier": "mtm",
            "state": "active",
            "importance_score": 0.5,
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "namespace": "mu/ws-1/ns-1/shared/*/s-1",
            "content_hash": "abc123",
        }
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.add("hello")

    assert isinstance(result, MemoryWriteResult)
    assert result.memory_id == "mem-1"
    assert result.content_hash == "abc123"
    assert result.namespace == "mu/ws-1/ns-1/shared/*/s-1"
    assert result.promoted is True  # tier "mtm" != "stm" -> promoted (client-side approximation)
    assert result.tiers_written == ("mtm",)
    assert result.events_emitted == ()  # default: the wire body carries no event list


# ---- plane-gating (BG-1: passing a private-plane field with a shared-only config rejects) -----


async def test_add_rejects_a_private_plane_field_with_no_network_call() -> None:
    """`MemoryClient` has no `SdkConfig.mode` toggle yet (Stage D) — it is unconditionally the
    SHARED-plane wire client, so `user=` (a private-plane field) is REJECTED, not silently
    dropped, and — crucially — rejected BEFORE any request is built (zero transport calls)."""
    transport = _transport({})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        with pytest.raises(PlaneFieldRejectedError) as exc_info:
            await client.add("hello", user="ada")
    assert exc_info.value.field == "user"
    assert exc_info.value.plane == "private"
    assert transport.calls == []  # type: ignore[attr-defined]


async def test_recall_rejects_a_session_field_with_no_network_call() -> None:
    transport = _transport({})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        with pytest.raises(PlaneFieldRejectedError) as exc_info:
            await client.recall("find me", session="s-99")
    assert exc_info.value.field == "session"
    assert transport.calls == []  # type: ignore[attr-defined]


async def test_build_context_rejects_a_user_field_with_no_network_call() -> None:
    transport = _transport({})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        with pytest.raises(PlaneFieldRejectedError) as exc_info:
            await client.build_context("find me", user="ada")
    assert exc_info.value.field == "user"
    assert exc_info.value.plane == "private"
    assert transport.calls == []  # type: ignore[attr-defined]


# ---- get() (net-new this phase) ----------------------------------------------------------------


async def test_get_sends_a_plain_get_to_the_placeholder_route() -> None:
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {
            "id": "mem-1",
            "content": "hello",
            "content_type": "text",
            "tier": "stm",
            "state": "active",
            "importance_score": 0.5,
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "namespace": "mu/ws-1/ns-1/shared/*/s-1",
        }
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.get("mem-1")

    assert isinstance(result, MemoryResponse)
    assert result.id == "mem-1"
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["method"] == "GET"
    assert recorded[0]["path"] == "/v1/memories/mem-1"


async def test_get_returns_none_on_a_404_rather_than_raising() -> None:
    transport: Transport = _RecordingTransport(
        response=TransportResponse(
            status_code=404,
            json_body={"detail": "memory mem-404 not found", "request_id": "req-1"},
        )
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.get("mem-404")
    assert result is None


# ---- build_context() (net-new this phase) ------------------------------------------------------


async def test_build_context_posts_to_the_context_window_placeholder_route() -> None:
    transport: Transport = _transport({"text": "find me", "items": [], "degraded": None})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.build_context("find me", limit=5, max_chars=2000)

    assert isinstance(result, ContextView)
    assert result.text == "find me"
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["path"] == "/v1/context/window"
    assert recorded[0]["json_body"] == {"text": "find me", "limit": 5, "max_chars": 2000}


async def test_build_context_with_no_explicit_limit_uses_the_settings_derived_default() -> None:
    transport: Transport = _transport({"text": "find me", "items": [], "degraded": None})
    settings = SdkSettings(
        base_url="http://unit-test.invalid",
        identity=SdkIdentity(
            user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="s-1"
        ),
        max_retries=0,
        default_recall_limit=7,
    )
    async with MemoryClient(settings=settings, transport=transport) as client:
        await client.build_context("find me")
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["json_body"]["limit"] == 7


# ---- share() (net-new this phase) --------------------------------------------------------------


async def test_share_posts_visibility_to_the_share_route() -> None:
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {
            "id": "mem-1",
            "content": "hello",
            "content_type": "text",
            "tier": "stm",
            "state": "active",
            "importance_score": 0.5,
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "namespace": "mu/ws-1/ns-1/shared/*/s-1",
        }
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.share("mem-1", visibility=Visibility.SHARED)

    assert isinstance(result, MemoryResponse)
    recorded = transport.calls  # type: ignore[attr-defined]
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["path"] == "/v1/memories/mem-1/share"
    assert recorded[0]["json_body"] == {"visibility": "shared"}


# ---- promote()/demote() (BG-3: named 501, zero network calls) ---------------------------------


async def test_promote_raises_the_named_not_implemented_error_with_zero_network_calls() -> None:
    transport = _transport({})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        with pytest.raises(SurfaceVerbNotImplementedError) as exc_info:
            await client.promote("mem-1", to_tier="mtm")
    assert exc_info.value.status_code == 501
    assert transport.calls == []  # type: ignore[attr-defined]  # never reached the wire at all


async def test_demote_raises_the_named_not_implemented_error_with_zero_network_calls() -> None:
    transport = _transport({})
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        with pytest.raises(SurfaceVerbNotImplementedError) as exc_info:
            await client.demote("mem-1", to_tier="stm")
    assert exc_info.value.status_code == 501
    assert transport.calls == []  # type: ignore[attr-defined]
