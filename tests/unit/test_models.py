"""Pure-unit: pydantic v2 model validation/serialization for the SDK's wire DTOs. No I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mu_contracts.domain.model.memory import Namespace, Visibility
from pydantic import ValidationError as PydanticValidationError

from mu_sdk.models.context import ContextIndexListView
from mu_sdk.models.memory import MemoryCreateRequest, MemoryListResponse, MemoryResponse
from mu_sdk.models.recall import (
    RecallChannels,
    RecallItemView,
    RecallMode,
    RecallRequest,
    RecallResult,
)

pytestmark = pytest.mark.unit


def test_memory_create_request_round_trips_and_omits_namespace() -> None:
    request = MemoryCreateRequest(content="hello", visibility=Visibility.SHARED)
    dumped = request.model_dump(mode="json", exclude_none=True)
    assert "namespace" not in dumped  # tenancy is header/auth-derived, never client-body-declared
    assert dumped["content"] == "hello"
    assert dumped["visibility"] == "shared"
    assert dumped["tier"] == "stm"  # default


def test_memory_create_request_rejects_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        MemoryCreateRequest(content="x", visibility=Visibility.SHARED, unknown_field="boom")  # type: ignore[call-arg]


def test_memory_create_request_rejects_empty_content() -> None:
    with pytest.raises(PydanticValidationError):
        MemoryCreateRequest(content="", visibility=Visibility.SHARED)


def test_memory_response_parses_full_frozen_shape() -> None:
    now = datetime.now(UTC).isoformat()
    response = MemoryResponse.model_validate(
        {
            "id": "mem-1",
            "content": "hello",
            "content_type": "text",
            "tier": "stm",
            "state": "active",
            "importance_score": 0.5,
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "namespace": "mu/ws/ns/shared/*/session",
        }
    )
    assert response.namespace == "mu/ws/ns/shared/*/session"  # string form, see module docstring
    assert response.metadata == {}
    assert response.parent_ids == []


def test_memory_list_response_holds_typed_items() -> None:
    now = datetime.now(UTC).isoformat()
    listing = MemoryListResponse.model_validate(
        {
            "memories": [
                {
                    "id": "mem-1",
                    "content": "hi",
                    "content_type": "text",
                    "tier": "stm",
                    "state": "active",
                    "importance_score": 0.5,
                    "access_count": 0,
                    "created_at": now,
                    "updated_at": now,
                    "namespace": "mu/ws/ns/shared/*/session",
                }
            ],
            "total": 1,
        }
    )
    assert len(listing.memories) == 1
    assert isinstance(listing.memories[0], MemoryResponse)


def test_recall_request_defaults() -> None:
    request = RecallRequest(text="find me")
    assert request.limit == 10
    assert request.mode is RecallMode.RANKED
    assert request.channels == RecallChannels(stm=True, mtm=True, ltm=True)


def test_recall_request_is_frozen() -> None:
    request = RecallRequest(text="find me")
    with pytest.raises(PydanticValidationError):
        request.text = "changed"  # type: ignore[misc]


def test_recall_result_memory_ids_projection_is_content_free() -> None:
    namespace = Namespace(
        org="org-1", workspace="ws-1", user="u-1", session="s-1", visibility=Visibility.SHARED
    )
    item = RecallItemView(
        memory_id="mem-1",
        content="hi",
        tier="stm",
        channel="stm",
        fused_score=1.0,  # type: ignore[arg-type]
    )
    result = RecallResult(
        namespace=namespace,
        items=[item],
        channels_run=RecallChannels(),
        generated_at=datetime.now(UTC),
    )
    assert result.memory_ids == ["mem-1"]


def test_context_index_list_view_allows_empty_indexes() -> None:
    view = ContextIndexListView(session_id="s-1", indexes=[], generated_at=datetime.now(UTC))
    assert view.indexes == []
