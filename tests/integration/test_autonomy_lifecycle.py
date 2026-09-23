from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

from app.config import Settings
from app.main import build_lifespan


class StubPool:
    isolated = False

    @asynccontextmanager
    async def acquire(self):
        yield object()


class AutonomyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_requires_desktop_capability_and_cancels_on_lifespan_exit(self) -> None:
        settings = Settings(
            _env_file=None,
            database_backend="turso",
            database_url="file::memory:",
            database_auth_token="synthetic-test-token",
            proactive_enabled=True,
            private_access_password="synthetic-test-password",
            auth_signing_secret="synthetic-test-signing-secret",
        )
        pool = StubPool()
        app = FastAPI(lifespan=build_lifespan(settings, lambda _settings: pool))
        scheduler_started = asyncio.Event()
        scheduler_cancelled = asyncio.Event()
        recovery_finished = False

        async def recovery(*_args, **_kwargs):
            nonlocal recovery_finished
            recovery_finished = True

        async def scheduler(**_kwargs):
            self.assertTrue(recovery_finished)
            scheduler_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                scheduler_cancelled.set()
                raise

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MINDCORE_DESKTOP_SHUTDOWN_CAPABILITY", None)
            with patch("app.main.hydrate_narrative_snapshot", new=AsyncMock()), \
                 patch("app.main.hydrate_self_model_snapshot", new=AsyncMock()), \
                 patch("app.main.recover_incomplete_turns", new=recovery), \
                 patch("app.main.run_autonomy_scheduler", new=scheduler):
                async with app.router.lifespan_context(app):
                    self.assertIsNone(app.state.autonomy_scheduler_task)

                os.environ["MINDCORE_DESKTOP_SHUTDOWN_CAPABILITY"] = "synthetic-test-capability"
                async with app.router.lifespan_context(app):
                    await asyncio.wait_for(scheduler_started.wait(), timeout=1)
                    task = app.state.autonomy_scheduler_task
                    self.assertIsNotNone(task)
                    self.assertFalse(task.done())
                self.assertTrue(scheduler_cancelled.is_set())
                self.assertTrue(task.cancelled())
