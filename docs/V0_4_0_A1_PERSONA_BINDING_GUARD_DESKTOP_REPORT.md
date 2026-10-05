# MindCore PC v0.4.0-A1 — Desktop Persona Binding Guard

Date: 2026-10-05 (Asia/Seoul)

## [Summary]

```text
V0_4_0_A1 = FAIL
TECHNICAL_ACCEPTANCE = FAIL
PERSONA_BINDING_GUARD = TRUE
HALF_LOGIN = TRUE
SHARED_RUNTIME_SAFETY = TRUE
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
CURRENT_NEW_REGRESSIONS = NOT_PROVEN (Android full instrumentation did not complete)
```

Desktop implements the shared Persona identity guard, half-login state, immutable first bind, and fail-closed protected actions. Desktop focused and regression checks passed. Overall acceptance remains FAIL because the paired Android instrumentation campaign did not complete, and the default Tauri DMG packaging step failed even though an app-only bundle build succeeded. No source fixes were made for lint or instrumentation issues.

## [Repository]

```text
DESKTOP_PATH = /Users/noseunghudong-alibujang/Developer/mindcore-desktop
DESKTOP_BRANCH = backup/mindcore-desktop-current-2026-09-30
DESKTOP_START_HEAD = c02cfb75c29236e1aad5e0a93ad8f7089b95b6fa
DESKTOP_FINAL_HEAD = c02cfb75c29236e1aad5e0a93ad8f7089b95b6fa
WORKING_TREE = implementation and acceptance-test changes present; pre-existing .toolchain/ retained
DIRTY_WORK_PRESERVED = YES
DIFF_CHECK = PASS
```

No commit, push, tag, or release was created. Existing untracked `.toolchain/` content was preserved.

## [Binding Contract]

```text
BOUND_MATCH_ALLOWED = PASS
UNBOUND_FIRST_BIND = PASS
BOUND_MISMATCH_FAIL_CLOSED = PASS
DB_UNAVAILABLE_FAIL_CLOSED = PASS
CONCURRENT_BIND_SAFE = PASS
CONFLICTING_BIND_SAFE = PASS
LOCAL_PERSONA_FORK_ON_BIND_FAILURE = 0
```

The guard reads durable schema metadata and atomically claims an unbound shared DB with a unique-key compare-and-set. A previously bound, different Persona is not overwritten. Protected authenticated write requests validate the binding before handling the action. Shared recovery/autonomy initialization is deferred until a matching identity has been validated. Mismatch and unavailable states are surfaced through the auth/session response and Sidebar with product-level Korean messages.

Desktop acceptance used synthetic local/shared-storage fixtures; it did not use authenticated production DB credentials or a live remote provider. The focused mismatch HTTP check observed a 409 before message/provider work, and the fresh-install shared-mode acceptance exercised a matching-bound send.

## [Desktop]

```text
BOUND_SEND = PASS (synthetic shared DB acceptance)
UNBOUND_BIND = PASS
MISMATCH_BLOCK = PASS
UNAVAILABLE_BLOCK = PASS
MISMATCH_PROVIDER_INVOCATIONS = 0 (focused guard path)
MISMATCH_DURABLE_MUTATIONS = 0 (focused guard path)
DESKTOP_PYTHON = 654 passed / 3 skipped / 380 subtests passed
DESKTOP_FRONTEND = 98 passed
DESKTOP_RUST = 60 passed
DESKTOP_FRONTEND_BUILD = PASS
DESKTOP_SIDECAR_BUILD = PASS (PyInstaller, Python 3.12.14)
DESKTOP_TAURI_BUILD = FAIL for default DMG bundling; app-only .app bundle PASS
```

The default `npm run tauri:build` completed the app build but failed in `bundle_dmg.sh`. A separate `npx tauri build --config src-tauri/tauri.updater.conf.json --bundles app` completed and produced the app bundle. This does not count as successful DMG packaging.

Compared with the previous Desktop baseline (651 passed / 3 skipped, 380 subtests), the current Python suite reports 654 passed / 3 skipped with the new guard and acceptance coverage. Frontend and Rust counts remain 98 and 60.

## [Cross Device]

```text
DESKTOP_BIND_VISIBLE_ANDROID = PASS (synthetic schema-24 shared file)
ANDROID_BIND_VISIBLE_DESKTOP = NOT_PROVEN as Android-first bind (metadata interoperability observed)
SAME_PERSONA_CONCURRENT_BIND = PASS (adapter fixtures)
CONFLICTING_PERSONA_BIND = PASS (single durable owner; loser mismatches)
MULTIPLE_BIND_WINNERS = 0
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
```

The interoperability check used one temporary schema-24 SQLite file through Desktop's libSQL/Turso adapter and Android's SQLite adapter. Desktop-first visibility was observed. Android's guard also validated the same metadata contract, but a separate Android-first bind followed by a Desktop read was not independently established. This is not evidence of connectivity or authentication against a remote shared DB deployment.

## [Environment variables]

Names found in Desktop project configuration include `DATABASE_BACKEND`, `DATABASE_URL`, `DATABASE_AUTH_TOKEN`, `LLM_PROVIDER`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `PRIVATE_ACCESS_PASSWORD`, `AUTH_SIGNING_SECRET`, `DIANA_TIMEZONE`, and `VITE_API_BASE_URL`. Tests in this run used synthetic fixtures; no real secret values were printed or used for remote acceptance.

## [Security and scope]

```text
BINDING_BYPASS_IN_RELEASE = NO (no test-only bypass introduced; app bundle built)
CONFIRMED_REAL_SECRETS = 0 (synthetic fixtures used; no secret values emitted)
PRODUCTION_SECURITY_WEAKENED = NO
DEVICE_PERSONA_REPLICA_REQUIRED = FALSE
MANUAL_SYNC_FILE_REQUIRED = FALSE
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
```

Legacy `.mindcorepair`, `.mindcoresync`, peer/replica sync, delta/envelope sync, remote-apply ledgers, tombstone convergence, conflict inbox/resolution, and offline divergent Persona growth were not used as the new architecture foundation.

## [Blockers and next]

- Android API 35 instrumentation discovered 76 methods, then reported a failure in untouched historical `M732SyncUiTest.pairingButtonsUseSafAndMutateRealTrustStore` (`dialog action unavailable: 연결`). A later legacy UI test stopped making progress. The run was interrupted after several minutes, so the full suite has neither zero failures nor zero not-run evidence.
- The existing API 36 AVD image was absent. Installing its system image stalled in SDK Manager and was stopped; no API 36 instrumentation rerun was possible.
- Default Tauri DMG packaging failed in `bundle_dmg.sh`; the app-only bundle succeeded.
- Android lint reported 5 errors and 15 warnings. The first reported error is the existing `MainActivity.onBackPressed` predictive-back lint. Lint findings were not changed.

```text
NEXT_DECISION = NO_GO (complete Android instrumentation and packaging evidence first)
DESKTOP_IMPLEMENTATION_COMMIT = NONE
DESKTOP_REPORT_COMMIT = NONE
```
