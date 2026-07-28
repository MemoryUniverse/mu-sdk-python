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

from mu_sdk.client import MemoryClient
from mu_sdk.models.consolidate import AskResult, ConsolidateResult
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
    now = datetime.now(UTC).isoformat()
    transport: Transport = _transport(
        {"facts_extracted": 2, "added": 1, "superseded": 1, "generated_at": now}
    )
    async with MemoryClient(settings=_settings(), transport=transport) as client:
        result = await client.consolidate(limit=25)

    assert isinstance(result, ConsolidateResult)
    assert result.superseded == 1
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
                "user": "alice",
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
                "user": "alice",
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
