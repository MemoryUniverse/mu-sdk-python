"""Pure-unit: `raise_for_wire_error` status-code -> typed-exception mapping table
(api-mcp-surface-spec.md §2.4/§8). No I/O — `TransportResponse` is constructed directly."""

from __future__ import annotations

import pytest

from mu_sdk.error_mapping import raise_for_wire_error
from mu_sdk.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PrivateDataRejectedError,
    RateLimitedError,
    ServerError,
    ServiceUnavailableError,
    UnexpectedResponseError,
    ValidationError,
)
from mu_sdk.transport import TransportResponse

pytestmark = pytest.mark.unit


def _response(status_code: int, body: dict[str, object] | None = None) -> TransportResponse:
    return TransportResponse(
        status_code=status_code,
        headers={"X-Request-ID": "req-123"},
        json_body=body if body is not None else {"detail": "boom", "request_id": "req-123"},
    )


@pytest.mark.parametrize(
    ("status_code", "expected_type"),
    [
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (409, ConflictError),
        (422, ValidationError),
        (500, ServerError),
        (502, ServerError),
        (599, ServerError),
        (300, UnexpectedResponseError),
        (600, UnexpectedResponseError),
    ],
)
def test_status_code_maps_to_expected_exception_type(
    status_code: int, expected_type: type[Exception]
) -> None:
    with pytest.raises(expected_type) as exc_info:
        raise_for_wire_error(_response(status_code))
    assert exc_info.value.status_code == status_code  # type: ignore[attr-defined]
    assert exc_info.value.request_id == "req-123"  # type: ignore[attr-defined]


def test_2xx_does_not_raise() -> None:
    raise_for_wire_error(_response(200))
    raise_for_wire_error(_response(201))
    raise_for_wire_error(_response(204))


def test_403_with_private_data_rejected_code_raises_the_specific_subclass() -> None:
    with pytest.raises(PrivateDataRejectedError):
        raise_for_wire_error(
            _response(403, {"detail": "no", "request_id": "r", "code": "private_data_rejected"})
        )


def test_403_without_the_code_raises_generic_authorization_error() -> None:
    with pytest.raises(AuthorizationError) as exc_info:
        raise_for_wire_error(_response(403, {"detail": "no", "request_id": "r"}))
    assert not isinstance(exc_info.value, PrivateDataRejectedError)


def test_429_extracts_retry_after_header() -> None:
    response = TransportResponse(
        status_code=429,
        headers={"Retry-After": "2.5"},
        json_body={"detail": "slow down", "request_id": "r"},
    )
    with pytest.raises(RateLimitedError) as exc_info:
        raise_for_wire_error(response)
    assert exc_info.value.retry_after_s == pytest.approx(2.5)


def test_503_extracts_retry_after_header() -> None:
    response = TransportResponse(
        status_code=503,
        headers={"Retry-After": "1"},
        json_body={"detail": "unavailable", "request_id": "r"},
    )
    with pytest.raises(ServiceUnavailableError) as exc_info:
        raise_for_wire_error(response)
    assert exc_info.value.retry_after_s == pytest.approx(1.0)


def test_malformed_body_falls_back_to_raw_text_never_crashes() -> None:
    response = TransportResponse(status_code=500, headers={}, json_body=None, text="oops")
    with pytest.raises(ServerError) as exc_info:
        raise_for_wire_error(response)
    assert "oops" in exc_info.value.message
