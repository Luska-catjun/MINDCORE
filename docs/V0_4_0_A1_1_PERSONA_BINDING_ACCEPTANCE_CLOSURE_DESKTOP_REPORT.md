# MindCore v0.4.0-A1.1 — Desktop Persona Binding Acceptance Closure

Date: 2026-10-05 (Asia/Seoul)

## [Summary]

```text
V0_4_0_A1_1 = PASS
V0_4_0_A1_FINAL = PASS
TECHNICAL_ACCEPTANCE = PASS
PERSONA_BINDING_GUARD = TRUE
HALF_LOGIN = TRUE
ANDROID_FIRST_BIND_VISIBLE_DESKTOP = TRUE
DMG_PACKAGING = PASS
CURRENT_NEW_REGRESSIONS = 0
```

This report closes the two A1.1 blockers relevant to Desktop: Android-first binding visibility through the real Desktop adapter/guard, and DMG packaging attribution. The historical A1 FAIL reports remain unchanged. No project source or test files were edited for A1.1.

## [Repository]

```text
DESKTOP_PATH = /Users/noseunghudong-alibujang/Developer/mindcore-desktop
DESKTOP_BRANCH = backup/mindcore-desktop-current-2026-09-30
DESKTOP_A1_BASE_HEAD = c02cfb75c29236e1aad5e0a93ad8f7089b95b6fa
DESKTOP_IMPLEMENTATION_COMMIT = 57525e87b44383dde489fb92a5b726bfc241d195
WORKING_TREE = pre-existing A1 implementation/tests and .toolchain/ retained; A1.1 report added
DIFF_CHECK = PASS
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
```

## [Android-first bind → Desktop]

```text
SYNTHETIC_SCHEMA = 24
INITIAL_DB_PERSONA = UNBOUND
ANDROID_BIND_RESULT = P (synthetic Persona ID 7f6fa614-3df8-4a78-97a2-31e4a2f7c850)
ANDROID_BIND_SUCCESS = YES
ANDROID_FIRST_BIND_DURABLE = PASS
ANDROID_FIRST_BIND_VISIBLE_DESKTOP = TRUE
DESKTOP_PROCESS = fresh Python process
DESKTOP_ADAPTER = app.database.turso.TursoPool / libSQL
DESKTOP_GUARD = app.database.persona_storage.validate_for_protected_action
DESKTOP_BINDING_STATE = BOUND_MATCH
DESKTOP_EXPECTED_PERSONA_EQUALS_DB_PERSONA = YES
DESKTOP_PROTECTED_ACTION_ALLOWED = YES
```

The Android device-side `ZM7333A211SharedDbTest.attachAndPersistSyntheticTurnOnSharedHranaDatabase` instrumentation was run with its existing explicit synthetic-DB arguments against a fresh schema-24 `sqld` database initially lacking a Persona owner. It completed successfully. Android bound the database and persisted a synthetic protected turn. After shutting down the Android test and database server, a separate Desktop Python process opened the same database file through `TursoPool`; the actual Desktop binding guard returned `BOUND_MATCH`, and the adapter read the Android-written messages. No production DB, account data, provider, or real credential was used. This demonstrates local adapter interoperability, not remote Turso authentication/connectivity.

Prior A1 reverse-direction evidence remains applicable: the Desktop-first shared-file test and cross-adapter host tests passed, and A1.1 made no production-source changes. No M7 stress campaign was repeated.

## [DMG attribution]

```text
EXACT_COMMAND = MINDCORE_UPDATER_DISABLED=1 npm run tauri:build (frontend/)
CURRENT_APP_BUNDLE = PASS
CURRENT_DMG = PASS
CURRENT_DMG_OUTPUT = frontend/src-tauri/target/release/bundle/dmg/MindCore_0.3.0_aarch64.dmg
BASELINE_DMG = PASS (clean detached worktree at c02cfb75c29236e1aad5e0a93ad8f7089b95b6fa)
A1_DMG_RELEVANT_FILES_TOUCHED = NO
DMG_FAILURE_CAUSAL_TO_A1 = NO
DMG_CLASSIFICATION = CURRENT_DMG_PASS
```

The first A1 attempt returned a Tauri wrapper error naming `bundle_dmg.sh`; the wrapper did not expose a failing inner shell step, so no inner-step cause is asserted. After detaching the leftover temporary image, the same command passed on both the clean baseline worktree and the current A1 tree, producing the `.app` and DMG. The final packaging status is PASS; the earlier transient failure is retained here as non-reproducible evidence, not relabeled as a known script defect. The A1 diff contains no Tauri/bundle configuration, resources, package metadata, signing settings, or build-script changes.

## [Reused A1 verification]

No A1 production source changed during A1.1. The accepted A1 host/build results remain:

```text
DESKTOP_PYTHON = 654 passed / 3 skipped (380 subtests passed)
DESKTOP_FRONTEND = 98 passed
DESKTOP_RUST = 60 passed
FRONTEND_PRODUCTION_BUILD = PASS
SIDECAR_BUILD = PASS
TAURI_APP_BUNDLE = PASS
TAURI_DMG = PASS (reproduced on baseline and current tree)
```

Focused binding markers remain as recorded in the historical A1 report: match allowed, unbound first-bind, mismatch fail-closed, database-unavailable fail-closed, concurrent same-Persona safety, and conflicting-bind single-winner safety all passed. Provider invocations and durable mutations on mismatch were zero; local fallback writes and Persona forks on failure were zero.

```text
DESKTOP_IMPLEMENTATION = PASS
DESKTOP_A1_1_REPORT = added
```
