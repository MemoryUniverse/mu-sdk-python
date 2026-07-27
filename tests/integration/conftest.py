"""Starts a REAL uvicorn instance of the conformance server (tests/conformance_server/app.py) for
every integration test — DEV-STANDARDS "integration tests use the real system, zero mocks": the
SDK's real `httpx.AsyncClient` (via `HttpxTransport`) makes real HTTP requests over real TCP to
this real ASGI server. Nothing here is a mock or an in-process ASGI transport shim.

A fresh module (hence fresh in-memory state) is loaded per test for isolation.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import pytest_asyncio
import uvicorn
from fastapi import FastAPI

_APP_PATH = Path(__file__).resolve().parent.parent / "conformance_server" / "app.py"


def _load_fresh_app() -> FastAPI:
    module_name = f"conformance_server_app_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, _APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load conformance server module from {_APP_PATH}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app
    if not isinstance(app, FastAPI):
        raise TypeError("conformance_server/app.py did not expose a FastAPI `app`")
    return app


@pytest_asyncio.fixture
async def conformance_base_url() -> AsyncIterator[str]:
    """Yields ``http://127.0.0.1:<port>`` of a live, freshly-started conformance server; tears it
    down (real graceful shutdown, not a kill) on teardown."""
    app = _load_fresh_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("conformance server failed to start within 5s")
        assert server.servers, "uvicorn reported started=True with no bound sockets"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serve_task
