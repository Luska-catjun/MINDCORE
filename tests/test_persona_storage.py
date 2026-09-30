import asyncio
import unittest
import libsql

from app.database.persona_storage import bind_persona_identity, storage_mode
from app.database.turso import TursoConnection
from app.config import Settings
from app.database.connection import create_pool


PERSONA_A = "13a7c634-f66d-490d-9e8f-04a2f9acd97c"
PERSONA_B = "df92e8b1-c3e6-49c1-90b1-47f870adbc78"


class PersonaStorageTests(unittest.TestCase):
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
