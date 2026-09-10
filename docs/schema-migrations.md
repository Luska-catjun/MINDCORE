# Forward schema migrations

MindCore creates an empty local libSQL/Turso database from
`db/turso/baseline_v1.sql`. That baseline describes released schema version
21. It is not a replay of `db/migrations/001` through `021`; those historical
files must never be automatically applied to an existing user database.

At backend startup and during the explicit desktop **Initialize** action,
MindCore classifies the database before mutating it:

- an empty database receives the baseline and a baseline ledger entry;
- a complete versionless legacy database is adopted once, without replaying
  historical scripts or altering user rows;
- a current released database receives a missing baseline ledger entry once;
- a partial, malformed, unknown, or newer database is rejected rather than
  guessed at or repaired;
- connection preflight remains `SELECT 1` only and never migrates a database.

The durable ledger is `schema_migration_ledger`. Every future migration has an
immutable ID, exact `from_version` and `to_version`, and a SHA-256 checksum.
Its DDL, ledger insert, and `schema_metadata.turso_baseline_version` update
run in one transaction. A failure rolls back all three, so restart can safely
retry the same registered step.

## Adding a future release migration

1. Add one explicit, forward-only `MigrationDefinition` to
   `app/database/migrations.py`. Definitions must form a contiguous sequence;
   never edit the ID, checksum, or SQL semantics of a released definition.
2. Update the canonical schema contract and fresh baseline for the new
   released version. The runner intentionally does not make an incomplete
   schema look current.
3. Add local file-backed libSQL tests for upgrade, rerun/idempotence,
   rollback, restart, and the final schema contract. Do not use a production
   database for migration tests.
4. Keep the desktop database preflight read-only. Only startup and explicit
   initialization own schema mutation.
