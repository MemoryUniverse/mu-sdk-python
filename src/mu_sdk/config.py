"""``SdkConfig``/``SharedPlaneConfig`` — the ONE config object that picks
:class:`~mu_sdk.client.MemoryClient`'s transport (design §3,
``2026-07-31-sdk-engine-server-design.md``) and, together with a populated ``shared``, composes the
private/shared dual-plane split (design §4). Build-plan Stage D, task D1.

**The import-boundary problem this module has to solve.** ``mu-sdk-python``'s own contract
(``pyproject.toml`` banner comment; ``.importlinter`` ``sdk-has-no-engine``) forbids importing
``mu_engine``/``mu_local`` at all — the default install is ``mu-contracts`` + ``httpx`` +
``pydantic``(-settings) + ``tenacity``, nothing heavier. But per A4
(``SDK-BUILD-DECISIONS.md``, ridden by the build-plan into this task's own brief), ``SdkConfig``
is PINNED to type its ``storage``/``embedder``/``model``/``lifecycle``/``manager_mode`` fields
against the REAL ``mu_local.config.StorageSettings``/``BackendChoice``/``ModelProfileSettings`` and
``mu_engine.lifecycle.settings.LifecycleSettings``/``mu_engine.lifecycle.mode_gate.ManagerMode``
classes — every one of those lives in a package this module may never import at runtime by
default.

**Resolution:** the standard "TYPE_CHECKING import, runtime ``Any`` alias" pattern. Under
``TYPE_CHECKING`` (never executed — a static-analysis-only branch) the REAL classes are imported,
so ``mypy``/IDEs see the pinned types exactly as A4/design §3 specify them. At runtime, when
``TYPE_CHECKING`` is ``False``, each name is instead bound to ``typing.Any`` — because this module
carries ``from __future__ import annotations`` (PEP 563), every field annotation below is a
*string* pydantic resolves against this module's own globals when it builds the validation schema;
resolving ``"StorageSettings | None"`` against a global where ``StorageSettings = Any`` yields a
schema equivalent to ``Any | None`` (i.e. "accept anything, validate nothing") — pydantic never
needs the real class to exist, so the default install (no ``mu-local``/``mu-engine`` installed)
still builds and instantiates ``SdkConfig`` cleanly (verified: a stand-in "heavy" type behind this
exact pattern round-trips through pydantic with and without the real import present). A caller who
installs the ``[embedded]`` extra (which pulls in ``mu-local`` -> ``mu-engine``, PACKAGING-v2 §1.3)
can pass real ``StorageSettings``/etc. instances; a caller who has not installed it can simply never
construct one (``mode="embedded"`` is the only path that ever reads these fields — see
``mu_sdk.transport.EmbeddedTransport``, which performs ITS OWN lazy ``mu_local`` import at
construction time, not at class-definition time either).

``auth``/``SharedPlaneConfig.auth`` are typed ``Any`` rather than ``mu_sdk.auth.SdkAuth`` for a
DIFFERENT, narrower reason verified directly against this pydantic version: ``SdkAuth`` is a plain
``typing.Protocol`` (not ``@runtime_checkable`` — ``mu_sdk/auth.py``, a file this task does not
own), and pydantic's arbitrary-type schema builder calls ``isinstance(value, SdkAuth)`` to validate
such a field, which raises ``TypeError: Instance and class checks can only be used with
@runtime_checkable protocols`` at class-*definition* time (reproduced directly: a pydantic
``BaseModel`` with a bare-Protocol field fails ``SchemaError`` before any instance is ever
constructed) — there is no pydantic-schema-only workaround short of editing ``auth.py`` (out of
scope here). ``Any`` accepts any duck-typed ``SdkAuth``-shaped object (anything exposing
``.headers() -> dict[str, str]``), which is exactly what every call site here and in
``mu_sdk.client``/``mu_sdk.transport`` actually needs — nothing in this codebase ever does
``isinstance(x, SdkAuth)`` either, so no behaviour is lost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:  # pragma: no cover — static-analysis only, see module docstring.
    from mu_engine.lifecycle.mode_gate import ManagerMode
    from mu_engine.lifecycle.settings import LifecycleSettings
    from mu_local.config import BackendChoice, ModelProfileSettings, StorageSettings
else:  # pragma: no cover — the runtime branch; see module docstring's pydantic-schema note.
    ManagerMode = Any
    LifecycleSettings = Any
    BackendChoice = Any
    ModelProfileSettings = Any
    StorageSettings = Any

__all__ = ["SdkConfig", "SharedPlaneConfig"]

_WIRE_MODES = ("local_server", "remote")  # modes with an HTTP transport — need `endpoint=`


class SharedPlaneConfig(BaseModel):
    """The shared (``mu-server``, always-remote) plane's own endpoint + auth (design §3/§4) —
    nested rather than a second set of top-level ``remote_*`` scalars on :class:`SdkConfig`, so a
    dual-plane caller (``mode="embedded"|"local_server"`` PLUS a populated ``shared=``) can supply
    the shared plane's transport details without colliding with the private plane's own
    ``endpoint``/``auth`` fields. Populating this on top of ``mode`` is what design §4 calls "the
    crossing" set-up — ``shared=None`` (the default) means single-plane, private-only."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    endpoint: str  # required — the shared plane is always remote (design §4 table)
    auth: Any  # required — remote plane always needs auth (§7.27); see module docstring re: Any


class SdkConfig(BaseModel):
    """The one config object that selects :class:`~mu_sdk.client.MemoryClient`'s transport
    (design §3). ``mode`` is ALWAYS the private-plane transport choice (or ``"remote"`` for a
    shared-only client); dual-plane is expressed by ALSO populating ``shared=`` (design §4) — it is
    never a second value packed into ``mode`` itself (an earlier draft's tuple shape, corrected by
    design §4's MAJOR-4 fix, is deliberately not reproduced here).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    mode: Literal["embedded", "local_server", "remote"]

    endpoint: str | None = None
    # ^ required for local_server/remote (this plane's own transport target); ignored for
    #   embedded (no wire at all, design §1.2/§5 "embedded mode has no wire at all").

    shared: SharedPlaneConfig | None = None
    # ^ populate to ADD the shared plane on top of `mode`'s private plane (design §4). None
    #   (default) = single-plane. NOTE (D1 scope): MemoryClient does not yet dispatch between a
    #   private and a shared transport per-call — that needs the frozen verb bodies' module-level
    #   `_PRIVATE_PLANE_CONFIGURED`/`_SHARED_PLANE_CONFIGURED` constants (client.py) to become
    #   real per-instance state, which is explicitly OUT of D1's scope ("verb bodies are frozen").
    #   D1 stores this value on the client for a future stage to wire; it has no effect yet.

    # -- embedded/local_server-only store + model config (A4-pinned types; see module docstring) --
    storage: StorageSettings | None = None  # A4: mu_local.config.StorageSettings (TYPE_CHECKING)
    embedder: BackendChoice | None = None  # convenience alias for storage.embedding
    model: ModelProfileSettings | None = None  # convenience alias for storage.llm

    manager_mode: ManagerMode | None = None  # client-level hint (api-sdk-mcp-surface-design §4.3)
    lifecycle: LifecycleSettings | None = None  # tier/salience knobs, passed through

    timeout_s: float = 30.0
    namespace: str = "default"
    workspace: str = "local"

    auth: Any = None
    # ^ EXISTS `SdkAuth`-duck-typed object (see module docstring re: Any); required for remote
    #   (validated below); local_server auto-loads a token from disk when this is None (design §1.2
    #   FIX 4 — see mu_sdk.transport.load_engine_server_token / MemoryClient.__init__).

    @model_validator(mode="after")
    def _validate_mode_shape(self) -> SdkConfig:
        """Fail-loud shape checks a caller cannot discover only by reading field defaults
        (DEV-STANDARDS rule 8) — never a silently-ignored misconfiguration."""
        if self.mode in _WIRE_MODES and not self.endpoint:
            raise ValueError(
                f"SdkConfig(mode={self.mode!r}) requires endpoint=... (design §3: 'required for "
                "local_server / remote')"
            )
        if self.mode == "remote" and self.auth is None and self.shared is None:
            # `shared` is irrelevant when mode IS "remote" (shared-only IS this plane, design §4:
            # "the shared plane's transport IS mode='remote' in this case") — checked here only so
            # the error message never fires for the (invalid on its own terms, but a different
            # concern) case of a `shared=` supplied alongside `mode="remote"`, which the dual-plane
            # doc explicitly scopes to embedded/local_server private planes only.
            raise ValueError(
                "SdkConfig(mode='remote') requires auth=... (design §3: 'required for remote', "
                "§7.27's api_key mode)"
            )
        if self.mode == "remote" and (
            self.storage is not None or self.embedder is not None or self.model is not None
        ):
            raise ValueError(
                "SdkConfig(mode='remote') owns no stores (design §3: 'embedded/local_server only; "
                "remote mode owns no stores') — storage/embedder/model must be left unset"
            )
        return self
