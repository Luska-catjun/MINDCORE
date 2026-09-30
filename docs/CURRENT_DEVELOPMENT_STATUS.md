# MindCore Desktop — Current Development Status

**Snapshot date:** 2026-09-30 (Asia/Seoul)  
**Scope:** the active Desktop source tree and the changes present at this snapshot. This is a handoff record, not a release or feature acceptance.

## Project and version

MindCore is an experimental persistent Persona runtime. It composes identity, conversation history, memory, knowledge, preferences, relationships, emotion, goals, decisions, episodes and other cognitive state around an LLM, then stores durable state in a Persona database.

The active source tree is `/Users/luska/mindcore-desktop-sync`, branch `feature/m73-desktop-sync`. It is the latest Desktop work tree found: its latest commit is `c44bad1` (2026-09-30), and it contains newer M7.3.3 shared-storage/recovery work than `/Users/luska/mindcore`, whose `main` is tagged `v0.2.0` from 2026-09-08 and is 38 commits behind its GitHub `origin/main`. The old clone also has local icon edits; it is not this handoff's source tree.

Version identifiers are not aligned with the planned release: `frontend/package.json` and `frontend/src-tauri/tauri.conf.json` both say `0.3.0`; the current Android/desktop handoff docs defer Persona connection UI and related release work to PC v0.4.0 + Android release. The checked-in version files therefore do **not** identify this working tree as v0.4.0. Treat the current state as M7.3.3-A.2.1.2.5 work on a v0.3.0 application baseline, with v0.4.0 still planned.

## Git snapshot and preservation

| Item | Snapshot |
|---|---|
| Local path | `/Users/luska/mindcore-desktop-sync` |
| Repository | Git repository; normal checkout, not a linked worktree |
| Branch / HEAD | `feature/m73-desktop-sync` / `c44bad12edd386224f13b0f1dadb28f2f0bd18b2` |
| Latest commit | `Document desktop recovery harness acceptance`, 2026-09-30 |
| `origin` | Local filesystem path `/Users/luska/mindcore-v030-turn-context`, not GitHub |
| Relation to `origin/feature/v030-turn-context` | 7 local commits ahead, 0 behind; merge base `072af164` |
| Existing changes at inspection | 25 modified tracked paths; 25 untracked status entries (one entry is the E2E artifact directory described below) |
| Staged changes at inspection | None |
| Existing GitHub `Luska-catjun/MINDCORE` | Public; not safe as a destination for this incomplete state |

The 7 local commits include M7.3.1.1 sync interoperability, Hrana write-stream handling, and the M7.3.3-A.2.1.2.5 recovery harness/acceptance record. The working tree also has the staged-for-review M7.3.3 candidate implementation, tests, and milestone reports described below. Changes were not reset, cleaned, stashed, or overwritten during this inspection.

The untracked `tests/m73213_ui_e2e/` directory is a local E2E test bench, not source. It contains a generated `.app`, Persona DBs, pair/identity files and `.env` files including local test credentials. Preserve this directory on the original Mac; do not add it to Git. The ignored `.venv`, `frontend/node_modules`, build outputs and native tool caches are generated dependencies/artifacts, not source. No ignored source file was identified.

## Structure and technology

- `app/`: Python 3.12 FastAPI backend and Persona runtime. `app/main.py` wires startup and API; `app/routers/` exposes routes; `app/services/mindcore/` contains cognition owners; `app/database/` contains schema/pool/storage boundaries.
- `frontend/`: React 19, TypeScript 6, Vite 8 UI. `frontend/src/components/` holds the UI; `frontend/src/api/` and `services/` handle backend and native calls.
- `frontend/src-tauri/`: Tauri 2 / Rust macOS and Windows application shell, Persona registry and sidecar lifecycle. Rust crate tests exercise native setup, registry and lifecycle rules.
- `desktop/`: packaged Python sidecar/build support. `scripts/`: migration, sync and acceptance helpers. `db/`: SQL schema and migrations. `tests/`: backend, sync, storage and UI acceptance tests.
- Backend uses FastAPI/Pydantic, `asyncpg`-shaped database access, libSQL/Turso support, provider adapters (Gemini, Groq and configured-compatible interfaces), and `TurnDurability` for durable turn/stage state. The desktop also retains native SQLite/local-only operation.

The current shared-Persona proposal uses one schema-24 database `P` and one stable `persona_id`. `LOCAL_ONLY` keeps a device-local SQLite database authoritative. `SHARED` is intended to connect Desktop and Android directly to the configured libSQL/Turso authority. Desktop storage classification/binding lives in `app/database/persona_storage.py`; database-token handling is in `app/services/persona_database_credentials.py`; sync and Hrana behavior are primarily in `app/services/persona_sync.py` and `persona_sync_crypto.py`.

## Implemented state

### Present in the codebase

- Persona identity and profile registry, chat history, provider selection, and durable message/turn storage.
- Cognition owners for memory, knowledge, preferences, internal/emotional state, relationship, goals/needs, decisions, intentions, episodes, narrative/self-model and related context construction. Durable post-cognition stages are coordinated through `TurnDurability` and recovery code.
- FastAPI local backend plus a React/Tauri desktop shell. The Tauri sidecar lifecycle, local auth/session boundary, native registry and startup/retry flows have dedicated tests.
- Local and remote libSQL/Turso storage plumbing, schema metadata checks, and Persona ID binding for the shared-storage candidate.
- Historical manual Persona replica-sync code and UI, encrypted pair/envelope logic and conflict documentation. This is a manual candidate, not proof of automatic/cloud/background sync.
- M7.3.3-A.2.1.2.5 test-only shared recovery harness: the report records same schema-24 synthetic Persona recovery entry points, deterministic provider/stage observation, two-device barrier, `sqld` lifecycle, Android outage-call observation and durable result inspection as PASS.

### Acceptance boundary and unfinished work

The latest accepted milestone in the current report sequence is **M7.3.3-A.2.1.2.5 = PASS**, which accepts that the recovery safety scenarios are callable and observable. It does not accept their safety outcomes. M7.3.3-A.2.1.2.6 remains the next gate for real provider replay prevention, cross-device exactly-once post-cognition, shared-DB outage behavior, local-fallback/Persona-fork checks and same-owner recovery sanity.

The older M7.3.3-A through A.2.1.2.4 reports retain FAIL/NO-GO or NOT-PROVEN outcomes. In particular, authenticated production-like remote DB acceptance and full Desktop↔Android safety are not established. Desktop's shared-storage candidate currently persists `DATABASE_AUTH_TOKEN` in a per-Persona environment file; the current architecture and credential-hardening reports identify OS-keychain-only DB-token storage and actual native runtime verification as open work. Capability markers remain conservative.

The v0.4.0/Android product work listed in `docs/ARCHITECTURE.md` remains deferred: Half-login/PersonaBindingGuard, Persona mismatch UX, Blue/Teal design system, neural-core boot screen, cleanup of legacy Sync/Devices UI and final Persona connection UI. No autonomous background runtime or notifications are delivered by this storage milestone. No standalone code TODO/FIXME markers were found in the inspected main source areas; `pass` statements found are primarily exception types and cleanup/error branches, not proof that the deferred product scope is implemented.

## Tests and verification on 2026-09-30

| Check | Result |
|---|---|
| Python `compileall` over `app desktop scripts tests` | PASS |
| Backend `pytest` suite | PASS: 646 passed, 1 skipped, 380 subtests passed; 1 warning. `pytest` was installed only into the ignored local `.venv` because it is not in the checked-in requirements files. |
| Frontend Vitest | PASS: 17 files, 98 tests |
| Frontend typecheck + production Vite build (`npm run build`) | PASS; Vite emitted one non-fatal dynamic-import chunk warning |
| Tauri/Rust (`cargo test --locked`) | PASS: 60 passed, 0 failed |
| Historical focused M7.3.3-A.2.1.2.5 harness evidence | PASS as recorded in the milestone report; provider replay and outage safety outcomes remain unproven |
| Health endpoint / packaged Tauri release | Not run in this snapshot; requires configured runtime state/native packaging path |

Test-only Python tooling is absent from `requirements.txt`; install `pytest` into the local virtual environment before running the suite. Do not add `.venv` to Git.

## Resume on another macOS account or machine

The private backup repository and `backup/mindcore-desktop-current-2026-09-30` branch are the handoff source. Clone it with GitHub CLI or Git; install a supported Python 3.12, Node/npm, and Rust toolchain (macOS Tauri prerequisites apply). Do not copy the original `.venv` or test `.env` files.

```sh
gh repo clone Luska-catjun/MINDCORE-Desktop-Private-Backup
cd MINDCORE-Desktop-Private-Backup
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r desktop/requirements.txt
python -m pip install pytest
cd frontend && npm ci
```

For the normal Tauri development launcher, set up a local `.env` from `.env.example` and frontend `.env` from `frontend/.env.example`; enter fresh provider/database credentials through local configuration or the app setup. Relevant variable names include `DATABASE_BACKEND`, `DATABASE_URL`, `DATABASE_AUTH_TOKEN`, `LLM_PROVIDER`, provider-specific `GEMINI_API_KEY`/`GROQ_API_KEY` (or the selected provider's key), `PRIVATE_ACCESS_PASSWORD`, `AUTH_SIGNING_SECRET`, `DIANA_TIMEZONE`, and `VITE_API_BASE_URL`. The checked-in examples contain names/placeholders only. Never copy the original test bench's credential values.

```sh
cd frontend
npm run tauri:dev
npm test -- --reporter=dot
npm run build
cd src-tauri && cargo test --locked
```

`npm run tauri:dev` builds/starts the sidecar and Tauri shell. For backend-only work, from the repository root run `python -m uvicorn app.main:app --reload` after local settings are supplied. Tests that require a real shared database, native Keychain, API provider, device pairing, or the two-device harness need their own synthetic test configuration. Never point acceptance tests at production Persona data.

## Next work, ordered from current evidence

1. Execute M7.3.3-A.2.1.2.6 with the existing Desktop/Android synthetic shared-DB harness; capture provider invocation counts, stage claim/mutation/completion counts, outage effects, and same-owner recovery.
2. Keep test scenarios on the shared synthetic database and verify exactly-once and fail-closed behavior before changing any capability marker.
3. Close Desktop DB-token persistence to OS-keychain-only storage and exercise a real native Desktop runtime through credential save, restart lookup and DB bind.
4. Run broad Desktop regressions and native build after the M7.3.3 safety changes; retain existing historical FAIL reports.
5. Once safety gates pass, take up only the v0.4.0 product items already listed in architecture docs and align Desktop/Android version metadata.

## Backup caveat

This status document is source-controlled in the Desktop private backup branch. The local `tests/m73213_ui_e2e/` runtime directory and root `.env`/credential files remain on the original Mac and are intentionally excluded because they contain generated databases, app binaries and credentials. Recreate synthetic fixtures on the new machine instead.
