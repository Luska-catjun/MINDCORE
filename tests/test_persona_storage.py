import asyncio
from contextlib import asynccontextmanager
import unittest
import libsql

from app.database.persona_storage import (
    PersonaBindingError,
    bind_persona_identity,
    inspect_binding_state,
    storage_mode,
    validate_for_protected_action,
)
from app.database.turso import TursoConnection
from app.config import Settings
from app.database.connection import create_pool


PERSONA_A = "13a7c634-f66d-490d-9e8f-04a2f9acd97c"
PERSONA_B = "df92e8b1-c3e6-49c1-90b1-47f870adbc78"


class PersonaStorageTests(unittest.TestCase):
    def test_concurrent_same_and_conflicting_first_bind_have_one_owner(self):
        class LockedConnection:
            def __init__(self, value): self.value = value
            @asynccontextmanager
            async def transaction(self):
                async with self.value.transaction(): yield
            async def execute(self, *args): return await self.value.execute(*args)
            async def fetchval(self, *args): return await self.value.fetchval(*args)

        class Pool:
            def __init__(self, connection): self.connection, self.lock = connection, asyncio.Lock()
            class Acquire:
                def __init__(self, pool): self.pool = pool
                async def __aenter__(self):
                    await self.pool.lock.acquire()
                    return self.pool.connection
                async def __aexit__(self, *_args):
                    self.pool.lock.release()
                    return False
            def acquire(self): return self.Acquire(self)

        async def scenario():
            raw = libsql.connect(":memory:")
            connection = LockedConnection(TursoConnection(raw))
            await connection.execute("create table schema_metadata(key text primary key,value text not null)")
            pool = Pool(connection)
            results = await asyncio.gather(
                validate_for_protected_action(pool, PERSONA_A),
                validate_for_protected_action(pool, PERSONA_A),
            )
            self.assertCountEqual(results, ["BOUND_NOW", "BOUND_MATCH"])
            self.assertEqual(await connection.fetchval(
                "select count(*) from schema_metadata where key=$1", "mindcore_persona_id"), 1)

            await connection.execute("delete from schema_metadata where key=$1", "mindcore_persona_id")
            results = await asyncio.gather(
                validate_for_protected_action(pool, PERSONA_A),
                validate_for_protected_action(pool, PERSONA_B),
                return_exceptions=True,
            )
            self.assertEqual(sum(value in {"BOUND_NOW", "BOUND_MATCH"} for value in results), 1)
            self.assertEqual(sum(isinstance(value, PersonaBindingError)
                                 and value.state == "BOUND_MISMATCH" for value in results), 1)
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", "mindcore_persona_id")
            self.assertIn(owner, {PERSONA_A, PERSONA_B})
            raw.close()
        asyncio.run(scenario())

    def test_protected_action_binding_states_and_failure_are_fail_closed(self):
        class Pool:
            def __init__(self, connection):
                self.connection = connection

            class _Acquire:
                def __init__(self, connection): self.connection = connection
                async def __aenter__(self): return self.connection
                async def __aexit__(self, *_args): return False

            def acquire(self): return self._Acquire(self.connection)

        class UnavailablePool:
            def acquire(self): raise OSError("synthetic unavailable")

        async def scenario():
            raw = libsql.connect(":memory:")
            connection = TursoConnection(raw)
            await connection.execute("create table schema_metadata(key text primary key,value text not null)")
            pool = Pool(connection)
            self.assertEqual(await inspect_binding_state(pool, PERSONA_A), "UNBOUND")
            self.assertEqual(await validate_for_protected_action(pool, PERSONA_A), "BOUND_NOW")
            self.assertEqual(await validate_for_protected_action(pool, PERSONA_A), "BOUND_MATCH")
            self.assertEqual(await inspect_binding_state(pool, PERSONA_A), "BOUND_MATCH")
            self.assertEqual(await inspect_binding_state(pool, PERSONA_B), "BOUND_MISMATCH")
            with self.assertRaises(PersonaBindingError) as mismatch:
                await validate_for_protected_action(pool, PERSONA_B)
            self.assertEqual(mismatch.exception.state, "BOUND_MISMATCH")
            with self.assertRaises(PersonaBindingError) as outage:
                await validate_for_protected_action(UnavailablePool(), PERSONA_A)
            self.assertEqual(outage.exception.state, "DB_UNAVAILABLE")
            raw.close()
        asyncio.run(scenario())

    def test_production_pool_rejects_all_http_endpoints(self):
        async def scenario():
            for url in ("http://example.invalid:33073", "http://192.168.1.20:33073"):
                settings = Settings(database_backend="turso", database_url=url,
                                    database_auth_token="synthetic-token")
                with self.assertRaisesRegex(ValueError, "shared_database_https_required"):
                    await create_pool(settings)
            settings = Settings(database_backend="turso", database_url="http://127.0.0.1:33073",
                                database_auth_token="synthetic-token")
            with self.assertRaisesRegex(ValueError, "shared_database_https_required"):
                await create_pool(settings)
            remote = Settings(database_backend="turso", database_url="http://example.invalid:33073",
                              database_auth_token="synthetic-token")
            with self.assertRaisesRegex(ValueError, "shared_database_https_required"):
                await create_pool(remote)
            lan = Settings(database_backend="turso", database_url="http://192.168.1.20:33073",
                           database_auth_token="synthetic-token")
            with self.assertRaisesRegex(ValueError, "shared_database_https_required"):
                await create_pool(lan)
        asyncio.run(scenario())

    def test_database_url_selects_local_only_or_shared_without_leaking_url(self):
        self.assertEqual(storage_mode("file:/tmp/synthetic.db"), "LOCAL_ONLY")
        self.assertEqual(storage_mode("libsql://synthetic.turso.io"), "SHARED")
        self.assertEqual(storage_mode("https://synthetic.turso.io"), "SHARED")
        with self.assertRaisesRegex(ValueError, "persona_storage_mode_invalid"):
            storage_mode("postgresql://synthetic.invalid")

    def test_identity_is_claimed_once_and_then_enforced(self):
        async def scenario():
            raw = libsql.connect(":memory:")
            connection = TursoConnection(raw)
            await connection.execute("create table schema_metadata(key text primary key,value text not null)")
            await bind_persona_identity(connection, PERSONA_A)
            self.assertEqual(await connection.fetchval(
                "select value from schema_metadata where key=$1", "mindcore_persona_id"), PERSONA_A)
            await bind_persona_identity(connection, PERSONA_A)
            with self.assertRaisesRegex(ValueError, "shared_persona_identity_mismatch"):
                await bind_persona_identity(connection, PERSONA_B)
            self.assertEqual(await connection.fetchval(
                "select value from schema_metadata where key=$1", "mindcore_persona_id"), PERSONA_A)
            raw.close()
        asyncio.run(scenario())

    def test_identity_rejects_invalid_persona_uuid(self):
        async def scenario():
            raw = libsql.connect(":memory:")
            connection = TursoConnection(raw)
            await connection.execute("create table schema_metadata(key text primary key,value text not null)")
            with self.assertRaisesRegex(ValueError, "persona_id_invalid"):
                await bind_persona_identity(connection, "not-a-persona")
            raw.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
