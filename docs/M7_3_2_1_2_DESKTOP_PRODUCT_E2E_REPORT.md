# MindCore M7.3.2.1.2 — Desktop Product E2E Report

## Verdict

**FAIL / NO-GO.** The Desktop candidate has source-level React → Tauri → sidecar wiring and local-file-only preflight. This milestone's required product E2E is not demonstrated: React tests mock Tauri invoke and file dialogs, while `scripts/m732_cross_platform_acceptance.py` invokes the backend CLI and Android bridge directly. Neither path proves a real product UI action reached the actual Tauri command and sync engine.

No Desktop code was changed for this closure review. The accumulated M7.3.2 candidate remains dirty and intact. No commit, push, tag, or release was made.

## Storage contract and wiring

- Scope: `MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS`.
- `manual_sync_scope` reports whether the selected Persona has a supported local absolute file database.
- `manual_sync_action` rejects unsupported storage before making the sync state directory or starting the packaged sidecar. The Rust unit test covers local acceptance and remote/relative rejection.
- `SyncPanel` calls `manual_sync_scope`, `manual_sync_action`, and Tauri's native open/save dialogs. Production source wiring exists; product-level execution was not run.
- React component tests use a mocked `invoke`; their success cannot count as actual pairing, export/import, trust mutation, revoke, conflict inbox, resolution, or persistence.
- The backend cross-platform acceptance script creates synthetic data and drives actual backend engines through CLI/bridge calls. It bypasses the React UI and native Tauri command, so it does not meet this milestone's gate.

## Product action matrix

| Workflow | Result | Evidence gap |
|---|---|---|
| Production Sync navigation/current device | FAIL | No launched product UI automation for this milestone. |
| Pair export/import/fingerprint/trust/restart/revoke | FAIL | No actual React click → dialog path → Tauri → sidecar state proof. |
| Secure export/import/replay | FAIL | No product UI artifact apply and backend state assertion. |
| Wrong Persona/tampered/unknown/revoked/malformed/unsupported | FAIL | Source/unit or mocked UX is not actual Tauri product action evidence; partial-mutation count remains unverified at product level. |
| Conflict inbox/mutable/tombstone/integrity resolution | FAIL | No UI-driven persisted conflict selection/preview/decision and resolution sync. |
| Desktop ↔ Android product pairing/sync/replay | FAIL | Existing backend exchange does not use both production UIs. |
| Restart/no-op final convergence | FAIL | No product restart and final no-op workflow. |

## Existing verification (from M7.3.2.1.1; not rerun here)

- Desktop Python: 621 pass / 622 run, one skip.
- Focused sync tests: 42/42 pass.
- React frontend: 98/98 pass (mock-based).
- Rust: 60/60 pass, including storage preflight.
- Frontend production build, Tauri dev build, and arm64 sidecar build: pass.
- Sidecar size: 25,974,288 bytes; 994 archive entries; synthetic fixture-name matches: 0.
- Bounded scan of 30 changed text files: zero private-key PEM or provider-token matches; five `api_key` assignments are explicit test placeholders in Rust unit tests (false positives), with zero actual credential findings. `git diff --check` was rerun for this closure and passed.

## Marker

```text
M7_3_2_1_2_DESKTOP_PRODUCT_E2E = FAIL
BASE_COMMIT = 43322b9030a2994a01fa81abd8978fcb9b2a8d9c
BRANCH = feature/m73-desktop-sync
PERSONA_SCHEMA_VERSION = 24
PERSONA_SYNC_SCOPE = MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS
LOCAL_FILE_STORAGE_CONTRACT = PASS (source + Rust unit coverage)
UNSUPPORTED_BACKEND_PREFLIGHT = PASS (unit level)
UNSUPPORTED_BACKEND_PRODUCT_PARTIAL_MUTATIONS = UNVERIFIED
REACT_TAURI_SOURCE_WIRING = PRESENT
REACT_TAURI_ENGINE_PRODUCT_E2E = FAIL
REACT_TESTS_USE_MOCK_INVOKE = YES
BACKEND_CLI_TEST_BYPASSES_REACT_TAURI_UI = YES
PAIRING_PRODUCT_MATRIX = FAIL
SYNC_IMPORT_EXPORT_PRODUCT_MATRIX = FAIL
CONFLICT_INBOX_PRODUCT_UI = FAIL
MUTABLE_AND_TOMBSTONE_RESOLUTION_PRODUCT_UI = FAIL
CROSS_PLATFORM_PRODUCT_UI_WORKFLOW = FAIL
FINAL_RESTART_AND_NO_OP_CONVERGENCE = FAIL
DESKTOP_PYTHON_TESTS = 621/622 (1 skipped; prior run)
DESKTOP_SYNC_FOCUSED_TESTS = 42/42 (prior run)
DESKTOP_FRONTEND_TESTS = 98/98 (prior run)
DESKTOP_RUST_TESTS = 60/60 (prior run)
FRONTEND_BUILD = PASS (prior run)
TAURI_BUILD = PASS (prior run)
SIDECAR_BUILD = PASS (prior run)
SIDECAR_SIZE_BYTES = 25974288 (prior run)
CHANGED_TEXT_SECRET_SCAN = 0 REAL SECRETS; 5 TEST-PLACEHOLDER FALSE POSITIVES
GIT_DIFF_CHECK = PASS
IMPLEMENTATION_COMMIT = NONE
FINAL_HEAD = 43322b9030a2994a01fa81abd8978fcb9b2a8d9c
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
NEXT = SYNC_PRODUCT_HOTFIX
```
