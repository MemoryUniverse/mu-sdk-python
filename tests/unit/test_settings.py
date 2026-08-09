"""Pure-unit: `SdkSettings` env-var loading (`pydantic-settings`, `MU_` prefix, `__` nesting).
No I/O beyond reading `monkeypatch`-scoped environment variables."""

from __future__ import annotations

import pytest
from mu_contracts.contracts.defaults import DEFAULT_CONSOLIDATE_LIMIT, DEFAULT_RECALL_LIMIT

from mu_sdk.settings import SdkSettings

pytestmark = pytest.mark.unit


def test_defaults_require_no_environment() -> None:
    settings = SdkSettings()
    assert settings.base_url == "https://api.memory-universe.dev"
    assert settings.timeout_s == 30.0
    assert settings.max_retries == 3
    assert settings.api_key is None
    assert settings.identity.is_complete() is False
    assert settings.default_page_limit == 10
    assert settings.default_recall_limit == 10
    assert settings.default_consolidate_limit == 50


def test_default_recall_limit_is_independently_configurable() -> None:
    """`default_recall_limit` must be its own knob, not coupled to `default_page_limit` —
    `search()` and `recall()` are distinct verbs tuned independently server-side."""
    settings = SdkSettings(default_page_limit=25, default_recall_limit=40)
    assert settings.default_page_limit == 25
    assert settings.default_recall_limit == 40


def test_default_consolidate_limit_is_independently_configurable() -> None:
    """`default_consolidate_limit` (Group D / C4) must be its own knob too — `consolidate()`'s
    sweep size is tuned independently of both `default_page_limit` and `default_recall_limit`."""
    settings = SdkSettings(default_recall_limit=40, default_consolidate_limit=75)
    assert settings.default_recall_limit == 40
    assert settings.default_consolidate_limit == 75


@pytest.mark.parametrize("bad_limit", [0, 101])
def test_default_recall_limit_is_bounded(bad_limit: int) -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        SdkSettings(default_recall_limit=bad_limit)


@pytest.mark.parametrize("bad_limit", [0, 1001])
def test_default_consolidate_limit_is_bounded(bad_limit: int) -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        SdkSettings(default_consolidate_limit=bad_limit)


def test_recall_and_consolidate_defaults_are_sourced_from_mu_contracts() -> None:
    """Group D / C4 (`CONFIG-AND-DATA-FIX-PLAN.md` §1.1): `SdkSettings`' own defaults must equal
    `mu_contracts`' `RecallDefaults` constants — the ONE shared source every one of the 8
    stray-literal sites now derives from, not a second, independently-typed `10`/`50`."""
    settings = SdkSettings()
    assert settings.default_recall_limit == DEFAULT_RECALL_LIMIT
    assert settings.default_consolidate_limit == DEFAULT_CONSOLIDATE_LIMIT


def test_reads_base_url_and_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MU_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("MU_API_KEY", "mu_live_from_env")
    settings = SdkSettings()
    assert settings.base_url == "http://127.0.0.1:9999"
    assert settings.api_key == "mu_live_from_env"


def test_reads_nested_identity_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MU_IDENTITY__USER_ID", "alice")
    monkeypatch.setenv("MU_IDENTITY__WORKSPACE_ID", "ws-1")
    monkeypatch.setenv("MU_IDENTITY__NAMESPACE_ID", "ns-1")
    monkeypatch.setenv("MU_IDENTITY__SESSION_ID", "sess-1")
    settings = SdkSettings()
    assert settings.identity.is_complete()
    assert settings.identity.user_id == "alice"


def test_rejects_unknown_field_passed_explicitly() -> None:
    """`extra="forbid"` applies to explicit construction kwargs — `pydantic-settings`' env
    source only ever looks up declared fields, so an unrelated `MU_`-prefixed env var is simply
    never read (covered by `test_reads_base_url_and_api_key_from_env` etc.); explicit unknown
    kwargs ARE rejected, which is what this test proves instead."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic's own validation error
        SdkSettings(totally_unknown_field="x")  # type: ignore[call-arg]


@pytest.mark.parametrize("bad_timeout", [0, -1.0])
def test_timeout_must_be_positive(bad_timeout: float) -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        SdkSettings(timeout_s=bad_timeout)
