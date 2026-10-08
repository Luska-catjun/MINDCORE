"""Portable native setup fixture: replace only network/credential I/O.

Invoked by Rust acceptance tests, never imported by the product or bundled.
The production Python setup entry point, provider validation/request handling,
and real libSQL SELECT/schema code run unchanged. This proves command
composition, not live remote service availability or Windows installed UI.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.desktop_backend import _setup_action
from app.database.turso import TursoPool
from app.services.gemini import GeminiError
from tests.m7333a2125_recovery_harness import SyntheticProviderObserver


async def run(action: str, config: str, database: str, fail_provider: bool) -> str:
    observer = SyntheticProviderObserver("OK")

    async def pool_factory(settings):
        assert action != "llm", "LLM must never open a database"
        assert settings.database_url == "libsql://wra-synthetic.invalid"
        assert settings.database_auth_token in {"wra-test-token", "wra-replacement-token"}
        return TursoPool(database, settings.database_auth_token)

    def provider_transport(request, timeout):
        assert action == "llm"
        assert "models/wra-test-model:generateContent" in request.full_url
        assert "key=wra-test-provider-key" in request.full_url
        if fail_provider:
            raise HTTPError(request.full_url, 404, "fixture", {}, BytesIO(b'{"error":"model not found"}'))
        reply = observer.invoke("wra-preflight", "synthetic-desktop")
        return BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": reply}]}}]}).encode())

    with patch("app.database.connection.create_pool", pool_factory), patch("app.services.gemini.urlopen", provider_transport):
        try:
            result = await _setup_action(action, config)
        except GeminiError as error:
            assert fail_provider and error.category == "model_or_api_version"
            return "PROVIDER_MODEL_FAILURE"
    if action == "llm":
        assert observer.snapshot()["provider_invocation_count"] == 1
    return result


if __name__ == "__main__":
    # No personal/global environment is allowed to fill missing fixture fields.
    token = os.environ.get("WRA_FIXTURE_TOKEN")
    os.environ.clear()
    if token is not None:
        os.environ["DATABASE_AUTH_TOKEN"] = token
    print(asyncio.run(run(*sys.argv[1:4], fail_provider=len(sys.argv) == 5)))
