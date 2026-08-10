"""Optional, opt-in auto-inject / auto-capture middleware (AGENT-INTEGRATION-AUDIT-AND-PLAN §4
Phase 5) — the "mem0-style middleware at the SDK layer" for agents that EMBED the SDK rather than
run `claude`/`codex` capture hooks.

**What it is.** A THIN, framework-agnostic wrapper an application puts around its own outbound LLM
call so that — WITHOUT the app ever calling `add`/`recall`/`build_context` explicitly — the SDK:

- **before** the LLM call: recalls the relevant prior context for the `(user, session)` and
  prepends the rendered window to the prompt (`MemoryClient.build_context` by default, or
  `MemoryClient.recall` when `inject_mode="recall"`);
- **after** the LLM call: captures the user turn and the assistant turn back into the store
  (`MemoryClient.add`).

**What it is NOT.** It adds NO new memory logic and NO new wire calls — every before/after step is
one of `MemoryClient`'s OWN canonical verbs (`build_context`/`recall`/`add`), which already run over
whichever REAL transport the client was constructed with (`mode="embedded"` in-process against real
stores, or `mode="local_server"` over HTTP to a real `mu-engine-server`). This module never touches
a store, an embedder, or the engine directly — it only ORCHESTRATES the client's existing verbs, so
it stays inside the SDK's `sdk-has-no-engine` import boundary (`.importlinter`), importing nothing
heavier than `mu_sdk.client`.

**Opt-in by construction.** Nothing here auto-wires into `MemoryClient`'s core verbs — a plain
`MemoryClient` is completely unchanged. An app opts in by explicitly constructing a
`MemoryMiddleware` around its client and routing its LLM call through `.run(...)` (or the
`.wrap(...)` decorator). `inject`/`capture` are independent on/off switches on `MiddlewareConfig`,
so an app can enable only auto-inject, only auto-capture, or both.

**Framework-agnostic.** The wrapper operates on a plain `str` prompt and a caller-supplied
`llm_call(prompt) -> str | Awaitable[str]` — nothing about a specific agent framework leaks in. A
caller whose framework speaks in message lists renders them to/from a string at its own boundary (or
supplies a custom `render`), exactly as a mem0 `add`/`search` integration does at the app edge.

Cancellation (DEV-STANDARDS rule 1): every `await` here is a direct await of a `MemoryClient` verb
or the caller's own `llm_call` — no `except Exception`/`except BaseException` wraps any of them, so
`asyncio.CancelledError` propagates untouched.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from mu_sdk.client import MemoryClient

__all__ = [
    "InjectMode",
    "LlmCall",
    "MemoryMiddleware",
    "MiddlewareConfig",
    "default_render",
]

InjectMode = Literal["context", "recall"]
"""How relevant memory is fetched before the LLM call:

- ``"context"`` — `MemoryClient.build_context` (a deterministically-rendered context WINDOW,
  the default; the whole window's `.text` is injected verbatim).
- ``"recall"`` — `MemoryClient.recall` (the ranked multi-channel read; each hit's `.content` is
  joined into a bullet list before injection).
"""

LlmCall = Callable[[str], "str | Awaitable[str]"]
"""The app's own outbound LLM call. Takes the (possibly memory-augmented) prompt, returns the
assistant text — SYNC (`str`) or ASYNC (`Awaitable[str]`) are both accepted (see `_invoke_llm`)."""


def default_render(context_text: str, prompt: str) -> str:
    """The default injection template — prepend the recalled context as a clearly-labelled block
    above the original prompt. Overridable via `MiddlewareConfig.render` for callers that want a
    different framing (e.g. a system-message wrapper, or a provider-specific delimiter)."""
    return f"Relevant memory from earlier:\n{context_text}\n\n{prompt}"


@dataclass(frozen=True)
class MiddlewareConfig:
    """The opt-in knobs. Defaults = "both auto-inject and auto-capture on, context-window
    injection". `user`/`session` set here are the DEFAULT tenancy for every turn; a per-call
    `user=`/`session=` on `MemoryMiddleware`'s methods overrides them (so one middleware instance
    can serve many `(user, session)` pairs)."""

    user: str | None = None
    session: str | None = None

    inject: bool = True
    """Master switch for the BEFORE step. When `False`, `before()` returns the prompt untouched and
    the client is never contacted for a read."""

    capture: bool = True
    """Master switch for the AFTER step. When `False`, `after()` is a no-op and the exchange is
    never written."""

    inject_mode: InjectMode = "context"
    inject_limit: int = 10
    inject_max_chars: int | None = None

    capture_user: bool = True
    """Capture the user's turn (the ORIGINAL prompt, never the memory-augmented one — see
    `run()`)."""
    capture_assistant: bool = True
    """Capture the assistant's returned turn."""

    render: Callable[[str, str], str] = field(default=default_render)
    """`(context_text, original_prompt) -> augmented_prompt`. Called only when there is non-empty
    context to inject; when the store returns nothing, the original prompt passes through
    unchanged."""


async def _invoke_llm(llm_call: LlmCall, prompt: str) -> str:
    """Call the app's LLM function, awaiting it when it is async. Kept isolated so `run()` reads
    linearly and both call styles are supported without duplicating the narrowing."""
    outcome = llm_call(prompt)
    if inspect.isawaitable(outcome):
        return await outcome
    return outcome


class MemoryMiddleware:
    """Wraps a REAL `MemoryClient` with the before/after auto-memory behavior (module docstring).

    Typical use::

        mw = MemoryMiddleware(client, MiddlewareConfig(user="ada", session="s1"))
        answer = await mw.run(user_prompt, my_llm_call)  # inject -> llm -> capture

    or as a decorator over the app's LLM function::

        chat = mw.wrap(my_llm_call)
        answer = await chat(user_prompt)  # same, per-call user=/session= allowed

    Every store interaction is delegated to `client`'s own verbs — this class holds no state beyond
    the client and its config, and constructs/closes nothing itself (the client's lifecycle stays
    the caller's, repository-pattern discipline)."""

    def __init__(self, client: MemoryClient, config: MiddlewareConfig | None = None) -> None:
        self._client = client
        self._config = config or MiddlewareConfig()

    @property
    def client(self) -> MemoryClient:
        return self._client

    @property
    def config(self) -> MiddlewareConfig:
        return self._config

    def _resolve(self, user: str | None, session: str | None) -> tuple[str | None, str | None]:
        """Per-call `user=`/`session=` win over the config defaults; either falls back to the
        config value (which itself may be `None`, letting the client apply its own default)."""
        return (
            user if user is not None else self._config.user,
            session if session is not None else self._config.session,
        )

    async def fetch_context(
        self, prompt: str, *, user: str | None = None, session: str | None = None
    ) -> str:
        """Read the relevant prior context for `(user, session)` and render it to a single string.
        Returns `""` when injection is disabled or the store has nothing — the caller (or `before`)
        treats an empty string as "inject nothing"."""
        if not self._config.inject:
            return ""
        resolved_user, resolved_session = self._resolve(user, session)
        if self._config.inject_mode == "context":
            view = await self._client.build_context(
                prompt,
                user=resolved_user,
                session=resolved_session,
                limit=self._config.inject_limit,
                max_chars=self._config.inject_max_chars,
            )
            return view.text
        result = await self._client.recall(
            prompt,
            user=resolved_user,
            session=resolved_session,
            limit=self._config.inject_limit,
        )
        return "\n".join(f"- {item.content}" for item in result.items)

    async def before(
        self, prompt: str, *, user: str | None = None, session: str | None = None
    ) -> str:
        """The BEFORE step: return `prompt` with the recalled context prepended via
        `config.render`, or `prompt` UNCHANGED when injection is off / the store is empty."""
        context_text = await self.fetch_context(prompt, user=user, session=session)
        if not context_text.strip():
            return prompt
        return self._config.render(context_text, prompt)

    async def after(
        self,
        user_text: str,
        assistant_text: str,
        *,
        user: str | None = None,
        session: str | None = None,
    ) -> None:
        """The AFTER step: capture the user turn and/or the assistant turn (per the
        `capture_user`/`capture_assistant` switches). A no-op when capture is disabled. Empty
        strings are skipped (nothing to store)."""
        if not self._config.capture:
            return
        resolved_user, resolved_session = self._resolve(user, session)
        if self._config.capture_user and user_text:
            await self._client.add(user_text, user=resolved_user, session=resolved_session)
        if self._config.capture_assistant and assistant_text:
            await self._client.add(assistant_text, user=resolved_user, session=resolved_session)

    async def run(
        self,
        prompt: str,
        llm_call: LlmCall,
        *,
        user: str | None = None,
        session: str | None = None,
    ) -> str:
        """The whole wrap in one call: inject-before -> `llm_call(augmented)` -> capture-after ->
        return the assistant text.

        The captured user turn is the ORIGINAL `prompt`, never the memory-augmented one — the
        injected context is a retrieval aid for THIS turn, not new content to re-store (re-storing
        it would compound recalled memory back into the store every turn)."""
        augmented_prompt = await self.before(prompt, user=user, session=session)
        assistant_text = await _invoke_llm(llm_call, augmented_prompt)
        await self.after(prompt, assistant_text, user=user, session=session)
        return assistant_text

    def wrap(self, llm_call: LlmCall) -> Callable[..., Awaitable[str]]:
        """Decorator form: return a memory-augmented version of `llm_call`. The returned coroutine
        function has the shape `async (prompt, *, user=None, session=None) -> str` and runs the
        full inject->call->capture wrap (`run`) on every invocation."""

        async def wrapped(
            prompt: str, *, user: str | None = None, session: str | None = None
        ) -> str:
            return await self.run(prompt, llm_call, user=user, session=session)

        return wrapped
