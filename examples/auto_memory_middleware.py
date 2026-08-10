"""Tiny example — auto-inject / auto-capture middleware over a REAL embedded MemoryClient.

Shows the whole point of `mu_sdk.middleware`: an app that NEVER calls `add`/`recall`/`build_context`
itself still (a) auto-injects relevant recalled context before its LLM call and (b) auto-captures
the exchange after — with a deterministic STUB "LLM" so the memory behavior is what's on display,
not the model.

Run it against the live dev stores (defaults to the local host-facing ports; override with
`MU_KV_URL` / `MU_QDRANT_URL` / `MU_FALKOR_HOST` / `MU_FALKOR_PORT`)::

    uv run python examples/auto_memory_middleware.py

Requires the `[embedded]` extra (`mu-local` importable) and the three stores reachable.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from mu_local.config import BackendChoice, StorageSettings

from mu_sdk import MemoryClient, MemoryMiddleware, MiddlewareConfig, SdkConfig


def _storage() -> StorageSettings:
    return StorageSettings(
        relational=BackendChoice(backend="sqlite"),
        kv=BackendChoice(
            backend="valkey",
            config={"url": os.environ.get("MU_KV_URL", "redis://127.0.0.1:16379/0")},
        ),
        vector=BackendChoice(
            backend="qdrant",
            config={"url": os.environ.get("MU_QDRANT_URL", "http://127.0.0.1:16333")},
        ),
        graph=BackendChoice(
            backend="falkordb",
            config={
                "host": os.environ.get("MU_FALKOR_HOST", "127.0.0.1"),
                "port": int(os.environ.get("MU_FALKOR_PORT", "16380")),
            },
        ),
    )


def stub_llm(prompt: str) -> str:
    """A deterministic stand-in for a real model. It answers the deploy question ONLY when the
    middleware has injected the memory block ("Relevant memory from earlier") carrying the fact — so
    the transcript makes the before-step visible without any real inference. A real app swaps this
    for its provider call; the middleware is unchanged."""
    injected = "Relevant memory from earlier" in prompt
    if "what is my deploy target" in prompt.lower():
        if injected and "staging-eu" in prompt:
            return "Your deploy target is staging-eu (recalled from earlier in this session)."
        return "I don't have any earlier context about your deploy target."
    return "Noted."


async def main() -> None:
    run_id = uuid.uuid4().hex[:8]
    config = SdkConfig(
        mode="embedded",
        storage=_storage(),
        workspace=f"example-ws-{run_id}",
        namespace=f"example-org-{run_id}",
    )
    client = MemoryClient(config=config)
    middleware = MemoryMiddleware(client, MiddlewareConfig(user="ada", session=f"chat-{run_id}"))

    # The app only ever calls `chat(...)` — no add/recall/build_context anywhere in app code.
    chat = middleware.wrap(stub_llm)

    turn1_prompt = "Remember: my deploy target is staging-eu."
    turn1_answer = await chat(turn1_prompt)
    print(f"[turn 1] user> {turn1_prompt}")
    print(f"[turn 1] bot>  {turn1_answer}\n")

    turn2_prompt = "What is my deploy target?"
    turn2_answer = await chat(turn2_prompt)
    print(f"[turn 2] user> {turn2_prompt}")
    print(f"[turn 2] bot>  {turn2_answer}")

    # Turn 2 answered correctly ONLY because the middleware auto-injected turn 1's fact — the app
    # never recalled anything itself.
    assert "staging-eu" in turn2_answer, "auto-inject did not surface turn 1's fact into turn 2"

    await client.aclose()
    print("\nOK — auto-inject + auto-capture worked with zero explicit add/recall calls.")


if __name__ == "__main__":
    asyncio.run(main())
