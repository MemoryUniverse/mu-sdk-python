"""CO-5 regression test — `EmbeddedTransport` must forward `user`/`session` to `LocalMemory` so
`mode="embedded"` honors the caller's namespace exactly like `mode="local_server"` does over the
wire (design §2.5 "Unified verb surface"; `mu_sdk.transport.EmbeddedTransport`'s own docstring).

Mirrors Stage A's AG-2 namespace-isolation proof (`2026-07-31-sdk-build-plan.md` §2 AG-2: "two
users... add() identical content... into the same [instance]... assert both... under their OWN
namespace, not one") at the SDK's public `MemoryClient` surface instead of `LocalMemory` directly
— the layer CO-5 actually fixed.

REAL stores (DEV-STANDARDS "zero mocks"): the live `mu-dev-cache`/`mu-dev-qdrant`/`mu-dev-falkordb`
containers, `StorageSettings` pointed at their host-facing ports explicitly (not `.env.test`, whose
relative `env_file` lookup is CWD-dependent and this package's tests run from a different CWD than
`mu-core/`). Needs the `[embedded]` extra installed (`mu-local` importable) — skipped, not failed,
when it is not (mirrors `EmbeddedExtraNotInstalledError`'s own opt-in discipline)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mu_sdk.client import MemoryClient
from mu_sdk.config import SdkConfig

pytestmark = pytest.mark.integration

mu_local = pytest.importorskip(
    "mu_local", reason="mode='embedded' needs the [embedded] extra (mu-local) installed"
)

from mu_local.config import BackendChoice, StorageSettings  # noqa: E402 — after importorskip


def _dev_storage() -> StorageSettings:
    """The live mu-dev-* stack's host-facing endpoints (docker-compose.dev.yml), spelled out
    explicitly rather than relying on `.env.test`'s CWD-relative discovery — see module
    docstring."""
    return StorageSettings(
        relational=BackendChoice(backend="sqlite"),
        kv=BackendChoice(backend="valkey", config={"url": "redis://localhost:16379/0"}),
        vector=BackendChoice(backend="qdrant", config={"url": "http://localhost:16333"}),
        graph=BackendChoice(backend="falkordb", config={"host": "localhost", "port": 16380}),
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[MemoryClient]:
    """One `MemoryClient(mode="embedded")` — the SAME `LocalMemory` instance both `ada` and `bo`
    add through (AG-2's own shape: one instance, two users, not two instances) — bound to a
    fresh, unique `workspace`/`namespace` pair so this run never collides with another test's or
    another agent's data on the shared dev stack."""
    uid = uuid.uuid4().hex[:12]
    config = SdkConfig(
        mode="embedded",
        storage=_dev_storage(),
        workspace=f"co5-ws-{uid}",
        namespace=f"co5-org-{uid}",
    )
    mem_client = MemoryClient(config=config)
    try:
        yield mem_client
    finally:
        await mem_client.aclose()


@pytest.mark.asyncio
async def test_embedded_add_lands_distinct_users_in_distinct_namespaces(
    client: MemoryClient,
) -> None:
    """The CO-5 bug, reproduced then closed: two users `add()` IDENTICAL content through the SAME
    embedded `MemoryClient`; pre-fix, `EmbeddedTransport._add` dropped `user`/`session` from the
    wire body entirely, so both calls silently landed in `LocalMemory`'s own
    `user="default"`/`session=None` partition — one shared namespace, not two. Post-fix, the
    receipt's own `namespace` (the resolved η `LocalMemory.add` actually wrote to) must differ."""
    content = f"co5-shared-marker-{uuid.uuid4().hex[:8]}"

    ada_receipt = await client.add(content, user="ada", session="s1")
    bo_receipt = await client.add(content, user="bo", session="s2")

    assert ada_receipt.namespace != bo_receipt.namespace, (
        "ada's and bo's add() receipts share one namespace — user/session were NOT forwarded to "
        "LocalMemory (the CO-5 bug)."
    )
    assert "ada" in ada_receipt.namespace
    assert "bo" in bo_receipt.namespace


@pytest.mark.asyncio
async def test_embedded_recall_does_not_cross_user_namespaces(client: MemoryClient) -> None:
    """`recall(user="ada")` must see ada's own memory and must NOT see bo's — the read-side twin
    of the write-side assertion above, over the same embedded client. Pre-fix, `_recall` also
    dropped `user`/`session`, so this recall would have run against `LocalMemory`'s shared
    default partition and returned BOTH users' content (a real cross-tenant leak, not a
    hypothetical one)."""
    ada_only_marker = f"ada-only-{uuid.uuid4().hex[:8]}"
    bo_only_marker = f"bo-only-{uuid.uuid4().hex[:8]}"

    await client.add(ada_only_marker, user="ada", session="s1")
    await client.add(bo_only_marker, user="bo", session="s2")

    ada_recall = await client.recall(ada_only_marker, user="ada", session="s1", limit=10)
    ada_contents = [item.content for item in ada_recall.items]
    assert any(ada_only_marker in c for c in ada_contents), (
        "ada's own recall(user='ada') did not surface her own just-added content."
    )
    assert not any(bo_only_marker in c for c in ada_contents), (
        "ada's recall(user='ada') surfaced bo's content — namespace isolation broken (CO-5)."
    )

    bo_recall = await client.recall(bo_only_marker, user="bo", session="s2", limit=10)
    bo_contents = [item.content for item in bo_recall.items]
    assert any(bo_only_marker in c for c in bo_contents)
    assert not any(ada_only_marker in c for c in bo_contents)
