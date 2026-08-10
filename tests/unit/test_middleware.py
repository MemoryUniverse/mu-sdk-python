"""Unit tests for `mu_sdk.middleware` — the PURE-logic / opt-in-gating half (no stores).

The STORE-TOUCHING proof (before-injects a real recalled fact, after-captures both turns) lives in
`tests/integration/test_middleware_auto_inject_capture.py`, which runs the REAL embedded engine
against the REAL dev stores (DEV-STANDARDS "zero mocks" — no store is ever mocked). These unit tests
cover only what needs NO store: that the opt-in switches actually gate, that no wire call is issued
when a step is disabled, and that the sync/async `llm_call` shapes and the render template behave.

To assert "the client is NOT contacted" without a live server, the `MemoryClient` under test is
constructed with a real `Transport` object (`_ExplodingTransport`) that FAILS if `request()` is ever
called — this is a genuine Transport implementation (the same seam the existing client unit tests
use, `mu_sdk.client`'s own docstring), not a mocked store: it proves the middleware issued no verb
at all, which is exactly the gating contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from mu_sdk.client import MemoryClient
from mu_sdk.middleware import (
    MemoryMiddleware,
    MiddlewareConfig,
    default_render,
)
from mu_sdk.transport import NullAuth, TransportResponse


class _ExplodingTransport:
    """A real `Transport` whose `request` must NEVER be reached in these tests — any call is a
    gating bug (a disabled step still hit the wire). Records nothing because reaching it at all is
    already a failure."""

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
        raise AssertionError(f"middleware issued a wire call while gated OFF: {method} {path}")

    async def aclose(self) -> None:
        return None


def _client() -> MemoryClient:
    """A real `MemoryClient` wired to the exploding transport — no network, no store. `NullAuth`
    (the embedded no-op) sidesteps credential resolution (there is no wire to authenticate to)."""
    return MemoryClient(transport=_ExplodingTransport(), auth=NullAuth())


def test_default_render_prepends_labelled_block() -> None:
    rendered = default_render("the fact", "the question")
    assert "the fact" in rendered
    assert rendered.endswith("the question")
    assert rendered.index("the fact") < rendered.index("the question")


async def test_inject_off_leaves_prompt_unchanged_and_issues_no_call() -> None:
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(inject=False, capture=False))
    out = await mw.before("hello", user="ada", session="s1")
    assert out == "hello"  # unchanged; _ExplodingTransport would have fired on any read
    await client.aclose()


async def test_fetch_context_off_returns_empty_string() -> None:
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(inject=False))
    assert await mw.fetch_context("anything", user="ada", session="s1") == ""
    await client.aclose()


async def test_capture_off_is_a_noop_and_issues_no_call() -> None:
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(capture=False))
    await mw.after("user turn", "assistant turn", user="ada", session="s1")  # no raise
    await client.aclose()


async def test_run_orchestrates_llm_with_gating_off_sync_call() -> None:
    """`run()` with both steps off still calls the LLM with the ORIGINAL prompt (no injection) and
    returns its output — proving the orchestration + sync `llm_call` path with zero store I/O."""
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(inject=False, capture=False))
    seen: list[str] = []

    def sync_llm(prompt: str) -> str:
        seen.append(prompt)
        return f"answer to: {prompt}"

    result = await mw.run("q1", sync_llm, user="ada", session="s1")
    assert seen == ["q1"]  # original prompt, un-augmented
    assert result == "answer to: q1"
    await client.aclose()


async def test_run_supports_async_llm_call() -> None:
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(inject=False, capture=False))

    async def async_llm(prompt: str) -> str:
        return f"async: {prompt}"

    result = await mw.run("q2", async_llm, user="ada", session="s1")
    assert result == "async: q2"
    await client.aclose()


async def test_wrap_returns_memory_augmented_callable() -> None:
    client = _client()
    mw = MemoryMiddleware(client, MiddlewareConfig(inject=False, capture=False))

    def sync_llm(prompt: str) -> str:
        return f"wrapped: {prompt}"

    chat = mw.wrap(sync_llm)
    assert await chat("q3", user="ada", session="s1") == "wrapped: q3"
    await client.aclose()


def test_per_call_tenancy_overrides_config_defaults() -> None:
    mw = MemoryMiddleware(
        MemoryClient(transport=_ExplodingTransport(), auth=NullAuth()),
        MiddlewareConfig(user="cfg-user", session="cfg-session"),
    )
    # config defaults apply when nothing is passed per-call
    assert mw._resolve(None, None) == ("cfg-user", "cfg-session")
    # per-call values win
    assert mw._resolve("call-user", "call-session") == ("call-user", "call-session")
    # partial override
    assert mw._resolve("call-user", None) == ("call-user", "cfg-session")


def test_custom_render_is_used() -> None:
    def loud(context_text: str, prompt: str) -> str:
        return f"<<{context_text}>>{prompt}"

    cfg = MiddlewareConfig(render=loud)
    assert cfg.render("ctx", "p") == "<<ctx>>p"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
