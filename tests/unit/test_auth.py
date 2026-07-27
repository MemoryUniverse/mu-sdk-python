"""Pure-unit: `SdkAuth` adapters + `resolve_auth` precedence. No I/O."""

from __future__ import annotations

import pytest

from mu_sdk.auth import ApiKeyAuth, BearerAuth, DemoHeaderAuth, resolve_auth
from mu_sdk.errors import AuthenticationError
from mu_sdk.settings import SdkIdentity, SdkSettings

pytestmark = pytest.mark.unit


def test_api_key_auth_produces_bearer_header() -> None:
    auth = ApiKeyAuth("mu_live_abc123")
    assert auth.headers() == {"Authorization": "Bearer mu_live_abc123"}


def test_api_key_auth_rejects_empty_key() -> None:
    with pytest.raises(AuthenticationError):
        ApiKeyAuth("")


def test_bearer_auth_produces_bearer_header() -> None:
    auth = BearerAuth("session-jwt")
    assert auth.headers() == {"Authorization": "Bearer session-jwt"}


def test_demo_header_auth_produces_all_four_identity_headers() -> None:
    identity = SdkIdentity(
        user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="sess-1"
    )
    auth = DemoHeaderAuth(identity)
    assert auth.headers() == {
        "X-Demo-User-Id": "alice",
        "X-Demo-Workspace-Id": "ws-1",
        "X-Demo-Namespace-Id": "ns-1",
        "X-Demo-Session-Id": "sess-1",
    }


def test_demo_header_auth_rejects_an_incomplete_identity() -> None:
    identity = SdkIdentity(user_id="alice", workspace_id="ws-1", namespace_id=None, session_id=None)
    with pytest.raises(AuthenticationError):
        DemoHeaderAuth(identity)


def test_resolve_auth_prefers_api_key_over_demo_identity() -> None:
    settings = SdkSettings(
        api_key="mu_live_abc",
        identity=SdkIdentity(
            user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="sess-1"
        ),
    )
    auth = resolve_auth(settings)
    assert isinstance(auth, ApiKeyAuth)


def test_resolve_auth_falls_back_to_demo_identity() -> None:
    settings = SdkSettings(
        identity=SdkIdentity(
            user_id="alice", workspace_id="ws-1", namespace_id="ns-1", session_id="sess-1"
        )
    )
    auth = resolve_auth(settings)
    assert isinstance(auth, DemoHeaderAuth)


def test_resolve_auth_raises_when_nothing_is_configured() -> None:
    with pytest.raises(AuthenticationError):
        resolve_auth(SdkSettings())
