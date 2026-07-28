"""The SDK's pydantic v2 request/response DTOs — its own mirror of the wire contract
(`mu_contracts.contracts` ships no `rest_schema` yet; see each submodule's docstring for the exact
authority + the documented gaps). Re-exports the full public DTO surface for ``from mu_sdk.models
import ...`` ergonomics."""

from __future__ import annotations

from mu_sdk.models.consolidate import AskRequest, AskResult, ConsolidateRequest, ConsolidateResult
from mu_sdk.models.context import ContextIndexListView, ContextIndexView
from mu_sdk.models.memory import MemoryCreateRequest, MemoryListResponse, MemoryResponse
from mu_sdk.models.recall import (
    RecallChannels,
    RecallItemView,
    RecallMode,
    RecallRequest,
    RecallResult,
)

__all__ = [
    "AskRequest",
    "AskResult",
    "ConsolidateRequest",
    "ConsolidateResult",
    "ContextIndexListView",
    "ContextIndexView",
    "MemoryCreateRequest",
    "MemoryListResponse",
    "MemoryResponse",
    "RecallChannels",
    "RecallItemView",
    "RecallMode",
    "RecallRequest",
    "RecallResult",
]
