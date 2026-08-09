"""Group D / C4 (`CONFIG-AND-DATA-FIX-PLAN.md` §1.1) — `EmbeddedTransport`'s three
`body.get("limit", <bare literal>)` fallbacks (sites #6/#7 of the 8-site inventory:
`transport.py:420,455` = `body.get("limit", 10)`, `transport.py:431` = `body.get("limit", 50)`)
now fall back to `mu_contracts.contracts.defaults.DEFAULT_RECALL_LIMIT`/`DEFAULT_CONSOLIDATE_LIMIT`
— the SAME shared source `mu-contracts`' own `RecallRequest`/`ContextWindowRequest`/
`ConsolidateRequest` and `mu-local`'s `LocalMemory` facade now read, instead of three more
independent bare literals.

Pure-unit (DEV-STANDARDS "mocks ONLY in pure unit tests"): `EmbeddedTransport` is constructed via
`object.__new__` (bypassing `__init__`, which builds a REAL `LocalMemory` over REAL
redis/qdrant/falkordb clients — needs the live `mu-dev-*` stack, covered by the `integration`-
marked `test_embedded_transport_namespace_parity.py` instead) with its `_memory` attribute
replaced by `_RecordingLocalMemory`, a minimal fake that records the `limit=` kwarg each verb
method receives and returns a small canned double — the same "fake the one injected dependency,
never mock the code under test" pattern `tests/unit/test_client.py`'s `_RecordingTransport`
already uses for `MemoryClient`. No store, no VM, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mu_contracts.contracts.defaults import DEFAULT_CONSOLIDATE_LIMIT, DEFAULT_RECALL_LIMIT

from mu_sdk.transport import EmbeddedTransport

pytestmark = pytest.mark.unit


class _CannedResult:
    """Duck-typed stand-in for `RecallResult`/`ContextView` — the only thing `EmbeddedTransport`
    does with either is call `.model_dump(mode="json")`."""

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {}


class _CannedConsolidateResult:
    """Duck-typed stand-in for `ConsolidateView` — `EmbeddedTransport._consolidate` reads exactly
    these three attributes off whatever `LocalMemory.consolidate()` returns."""

    facts_extracted = 0
    added = 0
    superseded = 0


@dataclass
class _RecordingLocalMemory:
    """Fakes the ONE method `LocalMemory` surface `EmbeddedTransport` calls per verb, recording
    the `limit=` kwarg it was actually invoked with."""

    recorded_limits: dict[str, int] = field(default_factory=dict)

    async def recall(self, query: str, *, limit: int, **_kwargs: Any) -> _CannedResult:
        self.recorded_limits["recall"] = limit
        return _CannedResult()

    async def consolidate(self, *, limit: int, **_kwargs: Any) -> _CannedConsolidateResult:
        self.recorded_limits["consolidate"] = limit
        return _CannedConsolidateResult()

    async def context(self, query: str, *, limit: int, **_kwargs: Any) -> _CannedResult:
        self.recorded_limits["context"] = limit
        return _CannedResult()


def _transport_over(fake_memory: _RecordingLocalMemory) -> EmbeddedTransport:
    """Builds an `EmbeddedTransport` WITHOUT running its real `__init__` (which needs a live
    `mu-local`/`mu-engine` composition over real stores) — see module docstring. The fake stands
    in for the real `LocalMemory` `EmbeddedTransport._memory` is typed to hold; the assignment is
    intentionally type-unsafe (that's the whole point of substituting a test double for it)."""
    instance = object.__new__(EmbeddedTransport)
    instance._memory = fake_memory  # type: ignore[assignment]
    return instance


@pytest.mark.asyncio
async def test_recall_falls_back_to_the_shared_recall_default_when_body_omits_limit() -> None:
    fake_memory = _RecordingLocalMemory()
    transport = _transport_over(fake_memory)

    await transport._recall({"text": "what did we discuss"}, {})

    assert fake_memory.recorded_limits["recall"] == DEFAULT_RECALL_LIMIT


@pytest.mark.asyncio
async def test_recall_still_honors_an_explicit_limit_in_the_body() -> None:
    """The fallback only fires when the wire body genuinely omits `limit` — an explicit value
    (e.g. resolved upstream from `SdkSettings.default_recall_limit`) must win."""
    fake_memory = _RecordingLocalMemory()
    transport = _transport_over(fake_memory)

    await transport._recall({"text": "q", "limit": 3}, {})

    assert fake_memory.recorded_limits["recall"] == 3


@pytest.mark.asyncio
async def test_consolidate_falls_back_to_the_shared_consolidate_default_when_body_omits_limit() -> (
    None
):
    fake_memory = _RecordingLocalMemory()
    transport = _transport_over(fake_memory)

    await transport._consolidate({})

    assert fake_memory.recorded_limits["consolidate"] == DEFAULT_CONSOLIDATE_LIMIT


@pytest.mark.asyncio
async def test_build_context_falls_back_to_the_shared_recall_default_when_body_omits_limit() -> (
    None
):
    fake_memory = _RecordingLocalMemory()
    transport = _transport_over(fake_memory)

    await transport._build_context({"query": "what did we discuss"})

    assert fake_memory.recorded_limits["context"] == DEFAULT_RECALL_LIMIT
