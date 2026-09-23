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
from time import perf_counter
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status

from app.database.schema_contract import (
    CURRENT_TURSO_BASELINE_VERSION,
    SCHEMA_VERSION_KEY,
    SchemaState,
    classify_turso_schema,
)
from app.database.migrations import (
    FORWARD_MIGRATIONS,
    SchemaBootstrapError,
    SchemaMigrationError,
    _current_schema_fast_path,
    _ledger_rows,
    _schema_version,
    _validate_ledger_rows,
    ensure_turso_schema_current,
)
from app.main import create_app
from app.services.startup_timing import emit_startup_timing


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
DESKTOP_SHUTDOWN_CAPABILITY_ENV = "MINDCORE_DESKTOP_SHUTDOWN_CAPABILITY"
DESKTOP_SHUTDOWN_CAPABILITY_HEADER = "X-MindCore-Desktop-Shutdown"
DESKTOP_INSTANCE_HEADER = "X-MindCore-Desktop-Instance"
SETUP_DIAGNOSTIC_PREFIX = "MINDCORE_SETUP_DIAGNOSTIC"


def _desktop_server_config(app: FastAPI, port: int) -> uvicorn.Config:
    """Use the sidecar's intentionally REST-only, pure-Python Uvicorn stack."""
    return uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        loop="asyncio",
        http="h11",
        ws="none",
    )


def _settings_from(config_path: str):
    from app.config import get_settings

    os.environ["MINDCORE_ENV_FILE"] = config_path
    get_settings.cache_clear()
    return get_settings()


def _setup_failure_diagnostic(action: str, config_path: str, error: Exception) -> str:
    """Return only non-secret setup metadata for a desktop development terminal."""
    llm_provider = "unavailable"
    llm_key_present = False
    settings_error: Exception | None = None
    try:
        settings = _settings_from(config_path)
        database_url = settings.database_url or ""
        database_url_present = bool(database_url)
        database_url_scheme = database_url.split(":", 1)[0].lower() if "://" in database_url else "invalid"
        database_token_present = bool(settings.database_auth_token)
        llm_provider = settings.llm_provider
        llm_key_present = bool(getattr(settings, f"{llm_provider}_api_key", None))
    except Exception as exc:
        settings_error = exc
        database_url_present = False
        database_url_scheme = "unavailable"
        database_token_present = False

    from app.services.llm_errors import LLMError
    from pydantic import ValidationError

    validation_field = "unavailable"
    validation_type = "unavailable"
    validation_error = error if isinstance(error, ValidationError) else settings_error
    if isinstance(validation_error, ValidationError):
        details = validation_error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        if details:
            location = ".".join(str(part) for part in details[0].get("loc", ()))
            error_type = str(details[0].get("type", ""))
            validation_field = "".join(
                character for character in location if character.isalnum() or character in "._"
            ) or "unavailable"
            validation_type = "".join(
                character for character in error_type if character.isalnum() or character in "._"
            ) or "unavailable"

    statement_index = "unavailable"
    object_name = "unavailable"
    if isinstance(error, SchemaBootstrapError):
        phase = "database_bootstrap"
        category = "schema_or_driver"
        statement_index = str(error.statement_index)
        object_name = "".join(
            character
            for character in error.object_name
            if character.isalnum() or character in "._-"
        ) or "unknown"
        error_class = error.exception_class
    elif isinstance(error, SchemaMigrationError):
        # The schema authority already reduces its errors to structural,
        # metadata-only identifiers.  Keep that classification available to
        # the native shell without disclosing the underlying database input.
        phase = "schema_migration"
        category = "schema"
    elif isinstance(error, LLMError):
        phase = "llm_preflight"
        category = error.category
    elif isinstance(error, (TimeoutError, ConnectionError)):
        phase = "database_connection" if action == "database" else "database_initialize"
        category = "connection"
    elif isinstance(error, OSError):
        phase = "database_connection" if action == "database" else "database_initialize"
        category = "native_or_network"
    elif isinstance(error, ValueError):
        phase = "database_connection" if action == "database" else "database_initialize"
        category = "driver_or_configuration"
    else:
        phase = {
            "database": "database_connection",
            "llm": "llm_preflight",
            "classify": "schema_classification",
            "initialize": "database_initialize",
        }.get(action, "setup")
        category = "setup"
    if not isinstance(error, SchemaBootstrapError):
        error_class = "".join(
            character for character in type(error).__name__ if character.isalnum() or character in "._"
        )
    return (
        f"{SETUP_DIAGNOSTIC_PREFIX} action={action} phase={phase} category={category} "
        f"statement_index={statement_index} object={object_name} "
        f"exception_class={error_class or 'Unknown'} "
        f"database_url_present={str(database_url_present).lower()} "
        f"database_token_present={str(database_token_present).lower()} "
        f"database_url_scheme={database_url_scheme} "
        f"llm_provider={llm_provider} llm_key_present={str(llm_key_present).lower()} "
        f"validation_field={validation_field} validation_type={validation_type}"
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

    @app.get("/_desktop/session", include_in_schema=False)
    async def desktop_session(request: Request) -> dict[str, str]:
        require_parent_capability(request)
        from app.routers.auth import create_session_token, require_auth_settings

        settings = request.app.state.settings
        require_auth_settings(settings)
        return {
            "access_token": create_session_token(
                settings,
                max_age_seconds=settings.auth_bearer_session_max_age_seconds,
            )
        }

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
            baseline = RESOURCE_ROOT / "db" / "turso" / "baseline_v1.sql"
            result = await ensure_turso_schema_current(
                connection, baseline_sql=baseline.read_text(encoding="utf-8")
            )
            return "BOOTSTRAPPED" if result.bootstrapped else "INITIALIZED"
    finally:
        await close_pool(pool)


def _benchmark_record(operation: str, started: float) -> None:
    """Print a fixed operation label and duration only."""
    allowed = {
        "select_1", "version_lookup", "ledger_validation", "fast_startup_schema",
        "integrity_check", "foreign_key_check", "full_classifier",
    }
    if operation in allowed:
        print(
            f"MINDCORE_STARTUP_BENCHMARK operation={operation} "
            f"elapsed_ms={round((perf_counter() - started) * 1000)}"
        )


async def _startup_readonly_benchmark(config_path: str) -> None:
    """Explicit schema-only benchmark. It performs no writes and prints no values."""
    from app.database.connection import close_pool, create_pool

    settings = _settings_from(config_path)
    if settings.database_backend.lower() != "turso":
        raise RuntimeError("benchmark_requires_turso")
    pool_started = perf_counter()
    pool = await create_pool(settings)
    print(
        "MINDCORE_STARTUP_BENCHMARK operation=pool_create "
        f"elapsed_ms={round((perf_counter() - pool_started) * 1000)}"
    )
    if pool is None:
        raise RuntimeError("database_not_configured")
    try:
        acquire_started = perf_counter()
        async with pool.acquire() as connection:
            print(
                "MINDCORE_STARTUP_BENCHMARK operation=pool_acquire "
                f"elapsed_ms={round((perf_counter() - acquire_started) * 1000)}"
            )

            started = perf_counter()
            await connection.fetchval("select 1")
            _benchmark_record("select_1", started)

            started = perf_counter()
            version_value = await connection.fetchval(
                "select value from schema_metadata where key=$1", SCHEMA_VERSION_KEY
            )
            _benchmark_record("version_lookup", started)
            safe_version = str(version_value) if str(version_value).isdecimal() else "unavailable"
            print(f"MINDCORE_STARTUP_BENCHMARK schema_version={safe_version}")

            started = perf_counter()
            version = await _schema_version(connection)
            rows = await _ledger_rows(connection)
            if version is None or not rows:
                raise RuntimeError("schema_authority_unavailable")
            _validate_ledger_rows(
                rows, registry=FORWARD_MIGRATIONS, current_version=version
            )
            _benchmark_record("ledger_validation", started)

            started = perf_counter()
            result = await _current_schema_fast_path(
                connection,
                registry=FORWARD_MIGRATIONS,
                target_version=int(CURRENT_TURSO_BASELINE_VERSION),
            )
            _benchmark_record("fast_startup_schema", started)
            print(f"MINDCORE_STARTUP_BENCHMARK current_schema={str(result is not None).lower()}")

            started = perf_counter()
            await connection.fetchval("pragma integrity_check")
            _benchmark_record("integrity_check", started)

            started = perf_counter()
            await connection.fetch("pragma foreign_key_check")
            _benchmark_record("foreign_key_check", started)

            started = perf_counter()
            report = await classify_turso_schema(connection)
            _benchmark_record("full_classifier", started)
            print(f"MINDCORE_STARTUP_BENCHMARK classifier_state={report.state.value}")
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
    parser.add_argument(
        "--startup-readonly-benchmark",
        action="store_true",
        help="Explicitly benchmark read-only Turso schema startup operations using --config.",
    )
    args = parser.parse_args()
    if args.startup_readonly_benchmark:
        if not args.config:
            raise SystemExit("--startup-readonly-benchmark requires an explicit --config path")
        try:
            asyncio.run(_startup_readonly_benchmark(args.config))
        except Exception as error:
            error_name = type(error).__name__
            safe_name = "".join(char for char in error_name if char.isalnum() or char == "_") or "Exception"
            print(f"MINDCORE_STARTUP_BENCHMARK status=failed error_type={safe_name}", file=sys.stderr)
            raise SystemExit(1) from None
        return
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
    config = _desktop_server_config(app, args.port)
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
    emit_startup_timing("uvicorn", "ready_route_registered", 0)

    # Loopback only: the packaged desktop backend is never a LAN server.
    server.run()


if __name__ == "__main__":
    main()
