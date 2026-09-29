# MindCore M7.3.1 — Desktop Sync Counterpart Report

**M7.3.1 = FAIL / NO-GO.** The Desktop candidate implements a schema-24 sync
engine, a protocol-v1 secure envelope service, OS keyring storage, and manual
file artifacts. Desktop-local checks passed. This does not establish accepted
Android/Desktop secure interoperability: no Android JVM, connected
instrumentation, APK build, or cross-device envelope/apply run was available.
The strict workdir procedure was also violated, so neither repository receives
an acceptance commit.

## Workspace and procedure

| Item | Result |
|---|---|
| Android base | `11707e007e2c1fcd4c2c1c31c1167422fdad6e2a`, `main` |
| Desktop sync branch/base | `feature/m73-desktop-sync` at `072af1640efbbc44bf36544a41d77a436fc08883` |
| Desktop source authority | Read-only; observed at `630ff6aac2565ee9a762573e576bec20b941b2ce` with three pre-existing modified files |
| Source authority mutation | None by this task |
| First shell filesystem action | Correct (`cd /Users/luska/mindcore-android && pwd`) |
| Parent traversal | Yes, through existing frontend scripts resolving `../desktop` and the sidecar builder's project-root resolution |
| Non-approved tool workdirs | 3: `frontend`, Desktop source authority, and M4.5 export repo |
| Forbidden production paths, Persona data, or credentials | Not accessed |
| `/Users/luska/diana` | Not accessed |
| M4.5 exporter | Read-only status inspection only; not modified |
| Push/tag/release | None |

The `frontend` install completed in that subdirectory. The updater-config test
and sidecar build scripts also resolved project files through parent-relative
paths. Source-authority and M4.5 status inspection used those repositories as
tool workdirs. Although the latter repositories were read-only, all three
workdirs and the parent-relative script paths violate the strict process
requirements. The process gate therefore fails.

## Candidate implementation and local evidence

- Added `app/services/persona_sync.py`, using the Desktop schema-24 contract
  and the Android protocol-v1 record, delta, tombstone, conflict, and apply
  semantics. Desktop sync metadata remains outside cognition schema 24.
- Added `app/services/persona_sync_crypto.py` for P-256 ECDH, HKDF-SHA256,
  AES-256-GCM, authenticated envelope fields, fingerprint trust, revocation,
  persistent replay state, and native OS keyring integration.
- Added `app/services/manual_sync_transport.py` and
  `scripts/mindcore_sync.py` for exclusive-create `.mindcorepair` and
  `.mindcoresync` artifacts and explicit local schema-24 SQLite snapshots.
  This is a manual CLI, not production Sync UI or live Turso synchronization.
- Pinned `cryptography==48.0.0` and `keyring==25.7.0`; included both in the
  PyInstaller sidecar dependency collection.
- Desktop policy inventory has 28 syncable entities. Directly loading the
  accepted Android Python implementation and comparing the synthetic record
  produced equal canonical JSON, equal SHA-256 hash
  `f8e1f1b0c8c2f7c55817739f5a65be37e1a0f0c7fecf78aba040c185da33e22d`, and
  equal policy tuples for all 28 entities. This is source-level vector evidence,
  not Android envelope interoperability evidence.
- Focused Desktop sync/security/transport tests: **35 passed**.
- Desktop existing Python suite: **615 passed, 1 skipped**.
- Desktop frontend tests: **91 passed**; frontend production build: **PASS**.
- Updater-config tests: **3 passed**; sidecar unit tests: **7 passed**.
- PyInstaller arm64 macOS sidecar build: **PASS**; archive dependency check:
  `SIDECAR_ARCHIVE_DEPENDENCIES_OK`.
- Tauri development executable build (`cargo build --manifest-path
  frontend/src-tauri/Cargo.toml`): **PASS** on macOS arm64.
- Built sidecar archive scan: **992 members**, no sync test modules,
  `.mindcorepersona` fixtures, or synthetic private-key fixtures found.
- Rust `cargo test --manifest-path frontend/src-tauri/Cargo.toml`: **59 passed**.
  Its first attempt raced the sidecar build and could not find the generated
  external binary; the retry after sidecar creation passed.
- Android Python suite: **91/91 passed**. Android JVM, connected
  instrumentation, Debug/Release APK builds, APK sizes/alignment, and actual
  Android/Desktop apply were not run: this checkout has no `gradlew`, and
  `adb`/`sdkmanager` are unavailable on PATH.
- Final `git diff --check`: **PASS**. Scoped secret scan: **13 files, 0
  matches**. Trailing-whitespace scan: **0 matches**.

## Interoperability and acceptance gaps

The existing Android M7.2.1.1 tests cover Android-owned secure apply and
recovery; they do not consume Desktop-generated encrypted artifacts. Desktop
tests exercise the Desktop implementation only. Consequently these M7.3.1
criteria remain **unverified**: cross-implementation secure envelope/AAD and
key derivation, both-direction real apply and replay, pairing persistence and
revoke across implementations, offline repeated rounds, mutable/tombstone
convergence and conflict classification across implementations, Persona A/B
isolation across real devices, and cross-implementation crash retry.

No provider generation, cognition replay, production Persona, or production
credential was used. `PERSONA_SYNC` remains `FALSE`; no conflict resolution or
production Sync UI was added. See [manual transport](SYNC_MANUAL_TRANSPORT.md)
and [convergence model](SYNC_CONVERGENCE_MODEL.md) for the candidate contract.

## Final marker

```text
MINDCORE_M7_3_1 = FAIL
ANDROID_BASE_COMMIT = 11707e007e2c1fcd4c2c1c31c1167422fdad6e2a
DESKTOP_SOURCE_COMMIT = 072af1640efbbc44bf36544a41d77a436fc08883
ANDROID_SCHEMA_VERSION = 24
DESKTOP_SCHEMA_VERSION = 24
SCHEMA_FORK_CREATED = NO
DESKTOP_SYNC_COUNTERPART = FALSE (candidate only; acceptance incomplete)
MANUAL_SECURE_TRANSPORT = FALSE (cross-implementation acceptance incomplete)
BIDIRECTIONAL_CONVERGENCE_FOUNDATION = FALSE (cross-implementation acceptance incomplete)
SYNC_FOUNDATION = TRUE
SECURE_SYNC_CORE = TRUE
REMOTE_APPLY_FOUNDATION = TRUE
PERSONA_SYNC = FALSE
FULL_COGNITION = TRUE
UI_PRODUCTION_READY = TRUE
AUTONOMY = FALSE
CROSS_IMPLEMENTATION_CANONICAL_HASH = PASS (one synthetic source-level vector)
CROSS_IMPLEMENTATION_ENTITY_POLICY = PASS (28/28 policy tuples)
CROSS_IMPLEMENTATION_DELTA = FAIL (not compared across running implementations)
CROSS_IMPLEMENTATION_CONFLICT_CLASSIFICATION = FAIL (not compared across running implementations)
CROSS_IMPLEMENTATION_SECURE_ENVELOPE = FAIL (no Android/Desktop envelope exchange)
DESKTOP_TO_ANDROID_APPLY = FAIL (not run)
ANDROID_TO_DESKTOP_APPLY = FAIL (not run)
DESKTOP_TO_ANDROID_REPLAY_IDEMPOTENCE = FAIL (not run)
ANDROID_TO_DESKTOP_REPLAY_IDEMPOTENCE = FAIL (not run)
OFFLINE_APPEND_CONVERGENCE = FAIL (Desktop-local only)
REPEATED_SYNC_CONVERGENCE = FAIL (not run cross-implementation)
SECOND_ROUND_EMPTY_OR_IDEMPOTENT = FAIL (not run cross-implementation)
DESKTOP_ONLY_MUTABLE_CONVERGENCE = FAIL (not run cross-implementation)
ANDROID_ONLY_MUTABLE_CONVERGENCE = FAIL (not run cross-implementation)
CONCURRENT_MUTATION_CONFLICT = FAIL (Desktop-local evidence only)
CONFLICT_AUTO_RESOLUTION_USED = NO
SILENT_LWW_MERGE_USED = NO
DESKTOP_TOMBSTONE_TO_ANDROID = FAIL (not run)
ANDROID_TOMBSTONE_TO_DESKTOP = FAIL (not run)
TOMBSTONE_IDEMPOTENCE = FAIL (not run cross-implementation)
DELETE_UPDATE_CONFLICT = FAIL (not run cross-implementation)
INDEPENDENT_COGNITION_CASCADE_DELETE = NO
MULTI_PERSONA_E2E_ISOLATION = FAIL (not run cross-implementation)
WRONG_PERSONA_REJECTED = YES (Desktop-local)
DESKTOP_REMOTE_APPLY_CRASH_SAFE = PASS (Desktop-local commit/checkpoint retry test)
ANDROID_REMOTE_APPLY_REGRESSION = FAIL (not run in this milestone)
PLAINTEXT_PERSONA_TRANSPORT = NO (Desktop artifact tests)
DESKTOP_PRIVATE_SYNC_KEY_PLAINTEXT = NO (OS keyring test)
ANDROID_PRIVATE_SYNC_KEY_EXPORTABLE = NO (M7.2.1.1 accepted baseline)
CENTRAL_SERVER_IMPLEMENTED = NO
ANDROID_PYTHON_TESTS = 91/91
ANDROID_JVM_TESTS = NOT RUN (Gradle wrapper unavailable)
ANDROID_INSTRUMENTATION = NOT RUN (adb unavailable)
DESKTOP_PYTHON_TESTS = 615 passed / 1 skipped
DESKTOP_FRONTEND_TESTS = 91/91
DESKTOP_RUST_TESTS = 59/59
E2E_SYNC_ACCEPTANCE = FAIL
ANDROID_DEBUG_BUILD = NOT RUN
ANDROID_RELEASE_BUILD = NOT RUN
DESKTOP_BUILD = PASS (frontend, PyInstaller sidecar, Tauri dev executable)
DESKTOP_TEST_FIXTURE_EXCLUSION = PASS (992-member sidecar archive)
SCOPED_SECRET_SCAN = PASS (13 files / 0 matches)
FIRST_FILESYSTEM_ACTION_CORRECT = YES
ANDROID_WORKDIR_COMPLIANCE = YES
DESKTOP_WORKDIR_COMPLIANCE = NO
NON_APPROVED_TOOL_WORKDIRS = 3
PARENT_TRAVERSAL_USED = YES (parent-relative frontend test/build script paths)
FORBIDDEN_PATH_ACCESSED = NO
DIANA_PATH_ACCESSED = NO
PRODUCTION_PERSONA_ACCESSED = NO
PRODUCTION_CREDENTIAL_ACCESSED = NO
DESKTOP_SOURCE_AUTHORITY_MODIFIED = NO
M45_REPO_MODIFIED = NO
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
ANDROID_COMMIT_SHA = NONE
DESKTOP_COMMIT_SHA = NONE
NEXT_DECISION = NO_GO
```
