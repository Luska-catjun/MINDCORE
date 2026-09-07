"""Fixed local entry point bundled as the MindCore desktop sidecar."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status

from app.database.schema_contract import SchemaState, classify_turso_schema
from app.main import create_app


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
DESKTOP_SHUTDOWN_CAPABILITY_ENV = "MINDCORE_DESKTOP_SHUTDOWN_CAPABILITY"
DESKTOP_SHUTDOWN_CAPABILITY_HEADER = "X-MindCore-Desktop-Shutdown"
DESKTOP_INSTANCE_HEADER = "X-MindCore-Desktop-Instance"
SETUP_DIAGNOSTIC_PREFIX = "MINDCORE_SETUP_DIAGNOSTIC"


def _settings_from(config_path: str):
    from app.config import get_settings

    os.environ["MINDCORE_ENV_FILE"] = config_path
    get_settings.cache_clear()
    return get_settings()


def _setup_failure_diagnostic(action: str, config_path: str, error: Exception) -> str:
    """Return only non-secret setup metadata for a desktop development terminal."""
    try:
        settings = _settings_from(config_path)
        database_url = settings.database_url or ""
        database_url_present = bool(database_url)
        database_url_scheme = database_url.split(":", 1)[0].lower() if "://" in database_url else "invalid"
        database_token_present = bool(settings.database_auth_token)
    except Exception:
        database_url_present = False
        database_url_scheme = "unavailable"
        database_token_present = False

    if isinstance(error, (TimeoutError, ConnectionError)):
        category = "connection"
    elif isinstance(error, OSError):
        category = "native_or_network"
    elif isinstance(error, ValueError):
        category = "driver_or_configuration"
    else:
        category = "setup"
    error_class = "".join(character for character in type(error).__name__ if character.isalnum() or character in "._")
    return (
        f"{SETUP_DIAGNOSTIC_PREFIX} action={action} category={category} "
        f"exception_class={error_class or 'Unknown'} "
        f"database_url_present={str(database_url_present).lower()} "
        f"database_token_present={str(database_token_present).lower()} "
        f"database_url_scheme={database_url_scheme}"
    )


def _parent_process_is_alive(pid: int) -> bool:
    """Check the native parent without using Unix-only signal semantics."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _ensure_desktop_auth_config(config_path: str) -> None:
    """Provision desktop-local auth once, without exposing either secret."""
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    existing_mode = path.stat().st_mode & 0o700 if os.name != "nt" else None
    secure_mode = (existing_mode or 0o600) if existing_mode is not None else None
    keys = {line.partition("=")[0] for line in text.splitlines() if "=" in line}
    additions = []
    if "PRIVATE_ACCESS_PASSWORD" not in keys:
        additions.append(f"PRIVATE_ACCESS_PASSWORD={secrets.token_urlsafe(32)}")
    if "AUTH_SIGNING_SECRET" not in keys:
        additions.append(f"AUTH_SIGNING_SECRET={secrets.token_urlsafe(32)}")
    if not additions:
        if secure_mode is not None:
            # Existing auth material must not retain group/other access either.
            os.chmod(path, secure_mode)
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        # mkstemp creates files as owner-only. Keep the mode explicit so a
        # permissive caller umask cannot broaden a file containing auth keys.
        if secure_mode is not None:
            os.fchmod(descriptor, secure_mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n") + "\n" + "\n".join(additions) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if secure_mode is not None and (path.stat().st_mode & 0o777) != secure_mode:
            # Preserve an existing owner's permissions while removing any
            # group/other access. A usual 0600 desktop config stays 0600.
            os.chmod(path, secure_mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _register_desktop_shutdown_route(app: FastAPI, server: object, capability: str | None) -> None:
    """Register parent-only lifecycle routes without exposing the capability."""

    def require_parent_capability(request: Request) -> None:
        candidate = request.headers.get(DESKTOP_SHUTDOWN_CAPABILITY_HEADER)
        if not capability or not candidate or not secrets.compare_digest(candidate, capability):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Desktop lifecycle access is not authorized.")

    @app.get("/_desktop/ready", include_in_schema=False)
    async def ready(request: Request) -> Response:
        require_parent_capability(request)
        return Response(
            status_code=204,
            headers={DESKTOP_INSTANCE_HEADER: capability},
        )

    @app.post("/_desktop/shutdown", include_in_schema=False)
    async def shutdown(request: Request) -> Response:
        require_parent_capability(request)
        # The capability is intentionally never written to a URL, argv, or log.
        server.should_exit = True
        return Response(status_code=204)


def _stop_when_parent_exits(server: object, parent_pid: int, poll_interval: float = 0.5) -> None:
    while not server.should_exit:
        if not _parent_process_is_alive(parent_pid):
            server.should_exit = True
            return
        time.sleep(poll_interval)


def _desktop_session_token(config_path: str) -> str:
    from app.routers.auth import create_session_token, require_auth_settings

    settings = _settings_from(config_path)
    require_auth_settings(settings)
    return create_session_token(settings, max_age_seconds=settings.auth_bearer_session_max_age_seconds)


async def _setup_action(action: str, config_path: str) -> str:
    from app.database.connection import close_pool, create_pool

    settings = _settings_from(config_path)
    if action == "llm":
        from app.services.llm import generate_reply

        # Preflight occurs before the managed identity is finalized. Use the
        # bundled generic template for this one minimal provider request;
        # never create user files during validation.
        settings.persona_identity_path = None
        await generate_reply(settings, "Reply with exactly: OK")
        return "LLM_CONNECTED"
    pool = await create_pool(settings)
    if pool is None:
        raise RuntimeError("database_not_configured")
    try:
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
            if action == "database":
                return "DATABASE_CONNECTED"
            report = await classify_turso_schema(connection)
            classification = (
                "INITIALIZED" if report.state == SchemaState.CURRENT else report.state.value
            )
            if action == "classify":
                return classification
            if action != "initialize":
                raise RuntimeError("unsupported_setup_action")
            if report.state == SchemaState.PARTIAL_OR_UNKNOWN:
                raise RuntimeError(f"partial_or_unknown: {report.details()}")
            if classification == "INITIALIZED":
                return "INITIALIZED"
            if report.state == SchemaState.COMPATIBLE_LEGACY:
                return "COMPATIBLE_LEGACY"
            baseline = RESOURCE_ROOT / "db" / "turso" / "baseline_v1.sql"
            async with connection.transaction():
                for statement in baseline.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        await connection.execute(statement)
            verified = await classify_turso_schema(connection)
            if verified.state != SchemaState.CURRENT:
                raise RuntimeError("schema_verification_failed")
            return "BOOTSTRAPPED"
    finally:
        await close_pool(pool)


def main() -> None:
    parser = argparse.ArgumentParser(description="MindCore local backend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--parent-pid", type=int, required=False)
    parser.add_argument("--setup-action", choices=("database", "llm", "classify", "initialize"))
    parser.add_argument("--config")
    parser.add_argument("--setup-print-generic-identity", action="store_true")
    parser.add_argument("--desktop-ensure-auth", action="store_true")
    parser.add_argument("--desktop-print-session", action="store_true")
    args = parser.parse_args()
    if args.setup_print_generic_identity:
        print((RESOURCE_ROOT / "app" / "prompts" / "identity_template.txt").read_text(encoding="utf-8"), end="")
        return
    if args.desktop_ensure_auth or args.desktop_print_session:
        if not args.config:
            raise SystemExit("desktop config is required")
        _ensure_desktop_auth_config(args.config)
        if args.desktop_print_session:
            print(_desktop_session_token(args.config))
        return
    if args.setup_action:
        if not args.config:
            raise SystemExit("setup config is required")
        try:
            print(asyncio.run(_setup_action(args.setup_action, args.config)))
        except Exception as error:
            # Setup UI intentionally receives a safe generic failure only.
            print(_setup_failure_diagnostic(args.setup_action, args.config, error), file=sys.stderr)
            print("SETUP_ERROR", file=sys.stderr)
            raise SystemExit(1)
        return
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    server = uvicorn.Server(config)

    if args.parent_pid:
        threading.Thread(
            target=_stop_when_parent_exits,
            args=(server, args.parent_pid),
            daemon=True,
        ).start()

    _register_desktop_shutdown_route(
        app,
        server,
        os.environ.get(DESKTOP_SHUTDOWN_CAPABILITY_ENV),
    )

    # Loopback only: the packaged desktop backend is never a LAN server.
    server.run()


if __name__ == "__main__":
    main()
