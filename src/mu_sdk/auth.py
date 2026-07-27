"""`SdkAuth` — the pluggable auth port (mu-local-and-sdk-spec.md §2.4).

Three adapters, one-to-one with the server-side `AuthPort` adapters
(api-sdk-mcp-surface-design.md §5.1: `demo` | `bearer`), plus the SERVER-mode API-key credential
(mu-local-and-sdk-spec.md §2.3/§5). `MemoryClient` resolves ONE of these at construction from
`SdkSettings` — never hardcodes a header literal outside this module.
"""

from __future__ import annotations

from typing import Protocol

from mu_sdk.errors import AuthenticationError
from mu_sdk.settings import SdkIdentity, SdkSettings

__all__ = ["ApiKeyAuth", "BearerAuth", "DemoHeaderAuth", "SdkAuth", "resolve_auth"]


class SdkAuth(Protocol):
    """Produces the request headers attached to every call."""

    def headers(self) -> dict[str, str]: ...


class ApiKeyAuth:
    """DEFAULT for SERVER mode — an issued API key presented as a bearer credential
    (mu-local-and-sdk-spec.md §2.4; port of `mem0.MemoryClient` header construction,
    `other_repos/mem0/mem0/client/main.py:87-94`)."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise AuthenticationError("MU API key not provided.")
        self._key = api_key

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}


class BearerAuth:
    """Interactive/user JWT (session tokens) — same header shape as `ApiKeyAuth`, different
    credential source (mu-local-and-sdk-spec.md §2.4)."""

    def __init__(self, token: str) -> None:
        if not token:
            raise AuthenticationError("MU bearer token not provided.")
        self._token = token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


class DemoHeaderAuth:
    """Loopback-dev-ONLY transport identity headers (api-mcp-surface-spec.md §2.2:
    `X-Demo-User-Id` / `X-Demo-Workspace-Id` / `X-Demo-Namespace-Id` / `X-Demo-Session-Id`).
    `auth_mode="demo"` performs NO authentication server-side — this adapter exists to drive the
    conformance server / a local dev `mu-server` instance, never a public bind."""

    def __init__(self, identity: SdkIdentity) -> None:
        user_id, workspace_id, namespace_id, session_id = (
            identity.user_id,
            identity.workspace_id,
            identity.namespace_id,
            identity.session_id,
        )
        if not (user_id and workspace_id and namespace_id and session_id):
            raise AuthenticationError(
                "DemoHeaderAuth requires a complete identity "
                "(user_id, workspace_id, namespace_id, session_id)."
            )
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._namespace_id = namespace_id
        self._session_id = session_id

    def headers(self) -> dict[str, str]:
        return {
            "X-Demo-User-Id": self._user_id,
            "X-Demo-Workspace-Id": self._workspace_id,
            "X-Demo-Namespace-Id": self._namespace_id,
            "X-Demo-Session-Id": self._session_id,
        }


def resolve_auth(settings: SdkSettings) -> SdkAuth:
    """Default auth resolution: an API key wins (SERVER mode); otherwise a complete demo
    identity (local/dev mode); otherwise fail loud at construction (DEV-STANDARDS rule 8 — port
    of mem0's "if not api_key: raise" fail-fast, `client/main.py:70-71`, generalized to either
    credential path)."""
    if settings.api_key:
        return ApiKeyAuth(settings.api_key)
    if settings.identity.is_complete():
        return DemoHeaderAuth(settings.identity)
    raise AuthenticationError(
        "No credentials configured: set `api_key` (SERVER mode) or a complete "
        "`identity` (local/dev demo mode) on SdkSettings."
    )
