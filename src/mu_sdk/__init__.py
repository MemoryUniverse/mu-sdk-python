"""mu-sdk — the Memory Universe Python developer SDK.

A THIN async wire client (`MemoryClient`): add / search / recall (tier-scoped) / consolidate / ask /
context, talking to `mu-server`'s public surface ONLY over the versioned wire contract. No engine,
no stores, no strategies, no embedder (see `CLAUDE.md`; `PACKAGING-v2.md` §4.2).
"""

from __future__ import annotations

from mu_sdk.auth import ApiKeyAuth, BearerAuth, DemoHeaderAuth, SdkAuth
from mu_sdk.client import ContextApi, MemoryClient
from mu_sdk.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PrivateDataRejectedError,
    RateLimitedError,
    SdkError,
    SdkTimeoutError,
    ServerError,
    ServiceUnavailableError,
    TransientSdkError,
    TransportError,
    UnexpectedResponseError,
    ValidationError,
)
from mu_sdk.settings import SdkIdentity, SdkSettings
from mu_sdk.transport import HttpxTransport, Transport, TransportResponse

__all__ = [
    "ApiKeyAuth",
    "AuthenticationError",
    "AuthorizationError",
    "BearerAuth",
    "ConflictError",
    "ContextApi",
    "DemoHeaderAuth",
    "HttpxTransport",
    "MemoryClient",
    "NotFoundError",
    "PrivateDataRejectedError",
    "RateLimitedError",
    "SdkAuth",
    "SdkError",
    "SdkIdentity",
    "SdkSettings",
    "SdkTimeoutError",
    "ServerError",
    "ServiceUnavailableError",
    "TransientSdkError",
    "Transport",
    "TransportError",
    "TransportResponse",
    "UnexpectedResponseError",
    "ValidationError",
]

__version__ = "0.1.0"
