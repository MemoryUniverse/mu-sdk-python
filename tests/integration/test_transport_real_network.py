"""Integration: `HttpxTransport` against REAL sockets (no conformance app needed for these two —
a refused connection and a real timeout are both genuine network conditions, not mocks)."""

from __future__ import annotations

import socket

import pytest

from mu_sdk.errors import SdkTimeoutError, TransportError
from mu_sdk.settings import SdkSettings
from mu_sdk.transport import HttpxTransport

pytestmark = pytest.mark.integration


def _closed_port() -> int:
    """Binds and immediately releases a port so nothing is listening there — a real
    connection-refused, not a simulated one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_connection_refused_raises_transport_error() -> None:
    port = _closed_port()
    settings = SdkSettings(base_url=f"http://127.0.0.1:{port}", timeout_s=2.0)
    transport = HttpxTransport(settings)
    try:
        with pytest.raises(TransportError):
            await transport.request("GET", "/health/live")
    finally:
        await transport.aclose()


async def test_unroutable_address_times_out() -> None:
    # TEST-NET-1 (RFC 5737): reserved for documentation, guaranteed unroutable -> real timeout.
    settings = SdkSettings(base_url="http://192.0.2.1", timeout_s=0.2)
    transport = HttpxTransport(settings)
    try:
        with pytest.raises(SdkTimeoutError):
            await transport.request("GET", "/health/live")
    finally:
        await transport.aclose()
