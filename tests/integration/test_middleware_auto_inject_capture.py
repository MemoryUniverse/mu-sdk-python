"""ACCEPTANCE PROOF for `mu_sdk.middleware` (AGENT-INTEGRATION-AUDIT-AND-PLAN §4 Phase 5) — an app
that makes NO explicit `add`/`recall`/`build_context` calls still accrues and reuses memory across
turns, proven against the REAL embedded engine over the REAL dev stores (DEV-STANDARDS "zero mocks":
no store is mocked anywhere here).

Shape (mirrors the plan's VERIFY): one `(user, session)`, a deterministic STUB "LLM" (a fixed
function — the LLM is NOT the thing under test, the MEMORY middleware is), across two turns:

- Turn 1 STATES a fact. The middleware auto-captures it (after-step) — no app-side `add`.
- Turn 2 ASKS about it. The middleware auto-injects the fact into turn 2's prompt (before-step,
  read from the REAL store) — no app-side `recall`/`build_context`.

Then two independent assertions:
1. **auto-inject**: turn 2's prompt (as the stub actually received it) carries the fact recalled
   from the real store, and the stub's answer reflects it.
2. **auto-capture**: a DIRECT Redis read of the STM tier finds BOTH turns' user text under this
   run's namespace — the exchange really landed in the store, not just in memory.

REAL stores: the live `mu-dev-cache`/`mu-dev-qdrant`/`mu-dev-falkordb` (host-facing ports, spelled
out explicitly — same discipline as `test_embedded_transport_namespace_parity.py`). Needs the
`[embedded]` extra (`mu-local` importable); skipped, not failed, when it is not.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mu_sdk.client import MemoryClient
from mu_sdk.config import SdkConfig
from mu_sdk.middleware import MemoryMiddleware, MiddlewareConfig

pytestmark = pytest.mark.integration

mu_local = pytest.importorskip(
    "mu_local", reason="mode='embedded' needs the [embedded] extra (mu-local) installed"
)
redis_asyncio = pytest.importorskip(
    "redis.asyncio", reason="direct-store-read verification needs redis-py (an mu-engine dep)"
)

from mu_local.config import BackendChoice, StorageSettings  # noqa: E402 — after importorskip

_KV_URL = "redis://127.0.0.1:16379/0"


def _dev_storage() -> StorageSettings:
    return StorageSettings(
        relational=BackendChoice(backend="sqlite"),
        kv=BackendChoice(backend="valkey", config={"url": _KV_URL}),
        vector=BackendChoice(backend="qdrant", config={"url": "http://127.0.0.1:16333"}),
        graph=BackendChoice(backend="falkordb", config={"host": "127.0.0.1", "port": 16380}),
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[MemoryClient]:
    uid = uuid.uuid4().hex[:12]
    config = SdkConfig(
        mode="embedded",
        storage=_dev_storage(),
        workspace=f"mw-ws-{uid}",
        namespace=f"mw-org-{uid}",
    )
    mem_client = MemoryClient(config=config)
    try:
        yield mem_client
    finally:
        await mem_client.aclose()


async def _stm_contents_for_namespace(namespace: str) -> list[str]:
    """DIRECT store read: pull every STM `MemoryItem` blob whose Redis key is scoped to this run's
    namespace (`mu/.../{namespace}/...:stm:mem:*`, `RedisMapper.memory_key`) and return each
    `content` — bypassing the SDK entirely, so a hit here proves the write really reached Redis."""
    import json

    rc = redis_asyncio.from_url(_KV_URL)
    contents: list[str] = []
    try:
        async for key in rc.scan_iter(match=f"mu/*{namespace}*:stm:mem:*", count=500):
            blob = await rc.get(key)
            if blob is None:
                continue
            text = blob.decode() if isinstance(blob, bytes) else str(blob)
            record = json.loads(text)
            content = record.get("content")
            if isinstance(content, str):
                contents.append(content)
    finally:
        await rc.aclose()
    return contents


async def test_middleware_auto_injects_and_auto_captures_across_turns(
    client: MemoryClient,
) -> None:
    uid = uuid.uuid4().hex[:8]
    fact_value = f"staging-eu-{uid}"
    namespace = client._config.namespace if client._config is not None else ""

    seen_prompts: list[str] = []

    def stub_llm(prompt: str) -> str:
        """Fixed, deterministic — surfaces the fact ONLY when it is present in the prompt it
        receives (i.e. only when the middleware injected it). The LLM has no memory of its own."""
        seen_prompts.append(prompt)
        if fact_value in prompt:
            return f"Your deploy target is {fact_value}."
        return "I have no earlier context about your deploy target."

    middleware = MemoryMiddleware(
        client,
        MiddlewareConfig(user="ada", session=f"chat-{uid}", inject_mode="context"),
    )
    chat = middleware.wrap(stub_llm)

    # --- Turn 1: state the fact. App makes NO add/recall call — only chat(...). ---
    turn1_prompt = f"Please remember: my deploy target is {fact_value}."
    await chat(turn1_prompt)
    # The before-step ran on an EMPTY store, so turn 1's prompt reached the stub un-augmented —
    # the injection point exists but had nothing to inject yet (the honest empty-store behavior).
    assert seen_prompts[0] == turn1_prompt, (
        "turn 1 prompt was altered though the store was empty "
        f"(stub saw: {seen_prompts[0]!r})"
    )

    # --- Turn 2: ask about it. App STILL makes NO recall call. Turn 2's OWN text has no fact. ---
    turn2_prompt = f"What is my deploy target? (ref {uid})"
    assert fact_value not in turn2_prompt  # the fact is not in what the app passes
    turn2_answer = await chat(turn2_prompt)

    # (1) AUTO-INJECT: the stub received turn 2's prompt with the fact spliced in from the REAL
    # store (and the render marker proves it came from the injection, not the app) — the app never
    # recalled anything itself.
    turn2_seen = seen_prompts[-1]
    assert fact_value in turn2_seen, (
        "middleware did NOT auto-inject turn 1's fact into turn 2's prompt "
        f"(prompt the stub saw: {turn2_seen!r})"
    )
    assert "Relevant memory from earlier" in turn2_seen, (
        "the injected block's render marker is absent — the fact did not arrive via injection"
    )
    assert turn2_seen != turn2_prompt, "turn 2 prompt was passed through un-augmented"
    assert fact_value in turn2_answer, "stub could not answer from the injected fact"

    # (2) AUTO-CAPTURE: a DIRECT Redis read finds BOTH turns' user text under this run's namespace.
    stored = await _stm_contents_for_namespace(namespace)
    assert any(fact_value in c for c in stored), (
        "turn 1's stated fact was not auto-captured into the real store "
        f"(namespace={namespace!r}, stored contents={stored!r})"
    )
    assert any(f"ref {uid}" in c for c in stored), (
        "turn 2's question was not auto-captured into the real store "
        f"(namespace={namespace!r}, stored contents={stored!r})"
    )


async def test_middleware_before_reads_injected_context_from_real_store(
    client: MemoryClient,
) -> None:
    """The before-step in isolation: after one captured turn, `before()` on a fresh query returns a
    prompt carrying the stored fact — read straight from the real embedded engine."""
    uid = uuid.uuid4().hex[:8]
    fact_value = f"prod-region-{uid}"
    middleware = MemoryMiddleware(client, MiddlewareConfig(user="bo", session=f"sess-{uid}"))

    # Seed one exchange through the wrap (no explicit add).
    await middleware.run(
        f"my primary region is {fact_value}", lambda _p: "noted", user="bo", session=f"sess-{uid}"
    )

    augmented = await middleware.before("which region do I use?", user="bo", session=f"sess-{uid}")
    assert (
        fact_value in augmented
    ), f"before() did not inject the stored fact from the real store (got: {augmented!r})"
