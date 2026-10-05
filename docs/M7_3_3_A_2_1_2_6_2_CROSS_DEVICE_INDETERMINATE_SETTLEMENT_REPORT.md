# M7.3.3-A.2.1.2.6.2 — Cross-Device Indeterminate Settlement

Date: 2026-10-05 (Asia/Seoul)

## Verdict

```text
M7.3.3-A.2.1.2.6.2 = FAIL
FOCUSED_ACCEPTANCE = PASS
TECHNICAL_ACCEPTANCE = FAIL (full Android instrumentation regression gate did not complete)
ROOT_CAUSE_IDENTIFIED = YES
CROSS_DEVICE_INDETERMINATE_SETTLEMENT = TRUE (focused shared-DB evidence)
TURN_DURABILITY_SHARED_DB = TRUE (focused hotfix contract plus Desktop durability suite)
SHARED_RUNTIME_SAFETY = PARTIAL
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
```

The hotfix and focused acceptance passed. The milestone remains FAIL because the required complete Android regression run did not finish cleanly, so `NEW_REGRESSIONS=0` is not proven. No implementation or report commit was made; no push, tag, or release was performed.

## Root cause

The provider boundary was durably recorded as `provider_generate=running`, but the two platform recovery selectors did not admit a foreign provider-incomplete turn:

- Desktop startup enters `app.main` → `recover_incomplete_turns` → `TurnDurability.incomplete_turn_ids` → `resume_turn` / `_resume_turn_unlocked`. `incomplete_turn_ids` selects only turns whose `core_completed_at` is non-null and status is `core_completed` or `partial`. `_resume_turn_unlocked` returns immediately when `core_completed_at` is null. Provider requests are not replayed there, but the pending foreground turn is omitted and remains pending.
- Android shared bind enters `TurnDurability.recover_foreground_turns(device_id=...)`. Its source-device predicate selects only turns whose user message `source_device` equals that installation ID. A foreign turn therefore never reaches `fail_foreground_turn` and remains pending.
- Before this change, Android owner recovery was the foreground path that mapped a running provider stage to `core_failed / PROVIDER_INDETERMINATE`. The Desktop recovery path had no corresponding foreground settlement.
- The explicit owner filter was the barrier to non-owner terminal settlement. The old Android failure path could be re-entered and rewrote failure timestamps; it did not provide a cross-device compare-and-set transition.

This was reproduced on the same schema-24 synthetic shared database: Android invoked the synthetic provider once, Desktop recovery without interruption evidence left all 10 Android-origin turns pending, and provider replay remained zero.

## Change

Added `TurnDurability.settle_interrupted_provider_turn` on both platforms. It requires an explicit interrupted turn identity and distinct origin/recovery device identities, checks a pending turn with no assistant result and a running provider stage, and changes the provider stage with a `running → failed` compare-and-set before recording `core_failed / PROVIDER_INDETERMINATE` in the same transaction. It does not call a provider. Already settled or otherwise ineligible turns are no-ops. No elapsed-time timeout or schema migration was introduced.

The accepted recovery harness supplies the interruption identity only after its deterministic provider-started/no-result interruption probe. Ordinary recovery without that evidence leaves a fresh provider-running turn unchanged. Harness controls and synthetic provider code are kept in test/debug source paths; the unsigned Android Release APK did not contain the debug recovery fixture, test sentinel, or Android test classes.

## Focused evidence

Evidence directory: `/Users/noseunghudong-alibujang/Developer/mindcore-desktop/.toolchain/m7333a2126-evidence/attempt2-6-2-hotfix-final3`.

```text
Schema / DB: schema 24, one local synthetic sqld storage, same Persona on Desktop and Android
Android-origin → Desktop: 10/10 settled; provider calls 10; Desktop replay 0
Desktop-origin → Android: 10/10 settled; provider calls 10; Android replay 0
Unique interrupted turns: 20
Total synthetic provider invocations: 20
Stale targeted turns left running after eligible recovery: 0
Terminal states: core_failed / PROVIDER_INDETERMINATE, 20/20
Assistant results on interrupted turns: 0
Owner reconnect / repeated recovery: PASS; terminal state and category unchanged
Concurrent cross-device settlement race: 5/5; one durable settlement per turn; duplicate transitions 0
Provider replay: 0
Duplicate assistant output: 0
Fresh provider callback held in flight across non-owner recovery: left pending/running; owner then completed successfully
Normal Desktop foreground turn: PASS
Normal Android foreground turn: PASS; complete with one assistant result and no indeterminate category
FOCUSED_FAILURES=0; FOCUSED_ERRORS=0; FOCUSED_CORE_SKIPS=0
```

There was no separate before-patch harness run. After the patch, unqualified Desktop recovery reproduced the same old symptom (0 replay, 10 pending), then explicit accepted interruption evidence settled those same turns. Five cross-device race turns are included in the 10 Desktop-origin settlement turns, not additional interrupted turns.

## Regression and build results

```text
Desktop Python: 651 passed, 3 skipped, 380 subtests passed
Frontend: 98 passed
Rust: 60 passed
Frontend production build: PASS
Sidecar build: PASS (arm64 macOS)
Tauri release executable build: PASS (--no-bundle)
Android Python: 102 passed, 11 subtests passed
Android JVM: 9 passed
Focused Android shared-runtime instrumentation: PASS
Android debug APK: PASS
Android unsigned Release APK: PASS
```

The complete `connectedDebugAndroidTest` run did not finish cleanly. It reported one failure in untouched legacy `M732SyncUiTest.pairingButtonsUseSafAndMutateRealTrustStore` on the API 35 AVD (`NullPointerException` when the test tried to click a missing `View`), then stopped making progress. The run was stopped after the stall. It had reported 30 skips and one failure at that point. This UI test is outside the hotfix changes, but no pre-hotfix full-suite run was available to establish its baseline; therefore `NEW_REGRESSIONS=0` remains NOT PROVEN.

## Security and repository state

The changed source, focused evidence, and Android release artifact were checked for common real API-key/token patterns: zero confirmed hits. The Android unsigned Release APK contained no debug recovery harness, synthetic token, or instrumentation classes. The Desktop sidecar contained no harness/test-driver marker. `PRODUCTION_SECURITY_WEAKENED=NO`.

```text
DESKTOP_START_HEAD = 7d3d4e62b4d8e4157ce64fe98c749d9ecfb756c1
DESKTOP_FINAL_HEAD = 7d3d4e62b4d8e4157ce64fe98c749d9ecfb756c1
ANDROID_START_HEAD = c8ba3a3b08df15074a66725f69e52b53a5f97bef
ANDROID_FINAL_HEAD = c8ba3a3b08df15074a66725f69e52b53a5f97bef
DIRTY_WORK_PRESERVED = YES
DESKTOP_IMPLEMENTATION_COMMIT = NONE
DESKTOP_REPORT_COMMIT = NONE
ANDROID_IMPLEMENTATION_COMMIT = NONE
ANDROID_REPORT_COMMIT = NONE
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
```

Next: resolve or establish a passing baseline for the legacy Android instrumentation failure, rerun the complete Android instrumentation regression gate, then perform the short final shared-runtime safety revalidation. Until then `SHARED_RUNTIME_SAFETY` remains `PARTIAL`.
