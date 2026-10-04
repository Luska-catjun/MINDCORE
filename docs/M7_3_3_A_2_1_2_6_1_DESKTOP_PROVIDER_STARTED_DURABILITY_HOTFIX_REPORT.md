# M7.3.3-A.2.1.2.6.1 Desktop Provider-Started Durability Hotfix Report

## [Summary]

M7.3.3-A.2.1.2.6.1 = PASS
TECHNICAL_ACCEPTANCE = PASS
ROOT_CAUSE_IDENTIFIED = YES
ORIGINAL_FAILURE_REPRODUCED = YES (two independent pre-patch executions)
PROVIDER_STARTED_DURABILITY = TRUE
DESKTOP_PROVIDER_START_BOUNDARY_SAFE = TRUE
DESKTOP_TO_ANDROID_PROVIDER_REPLAY_SAFE = TRUE
SHARED_RUNTIME_SAFETY = PARTIAL

This PASS covers only the Desktop foreground provider-start boundary and its Desktop-origin Android replay check. M7.3.3-A.2.1.2.6 remains FAIL/PARTIAL; the reverse direction, outage cases, completed-cognition rerun cases, and stage-claim race remain unproven. The accepted run used a synthetic schema24 shared DB, with no production Persona data or external provider credentials.

## [Repository]

DESKTOP_PATH = /Users/noseunghudong-alibujang/Developer/mindcore-desktop
DESKTOP_BRANCH = backup/mindcore-desktop-current-2026-09-30
DESKTOP_START_HEAD = f2eef62dac416729891f54887f13b95821977c4a
DESKTOP_FINAL_HEAD = f2eef62dac416729891f54887f13b95821977c4a
DESKTOP_WORKTREE = dirty; authorized changes and pre-existing dirty work preserved
ANDROID_PATH = /Users/noseunghudong-alibujang/Developer/mindcore-android
ANDROID_BRANCH = backup/mindcore-android-current-2026-09-30
ANDROID_START_HEAD = 332395077a9d77a5c00d0312e4fef7d740896cf2
ANDROID_FINAL_HEAD = 332395077a9d77a5c00d0312e4fef7d740896cf2
ANDROID_WORKTREE = dirty; authorized debug/test changes and pre-existing dirty work preserved
DIRTY_WORK_PRESERVED = YES
SCHEMA_CHANGED = NO (schema24)

Both HEADs remain at the requested backup commits. No reset, clean, stash, restore, rebase, amend, commit, push, tag, or release was performed. The previous .2.6 reports and evidence remain intact. The prior dirty-work snapshot and hash manifest are under Android .toolchain/m7333a2126-evidence/attempt2-start/.

## [Original Failure]

ORIGINAL_FAILURE_REPRODUCED = YES (two independent pre-patch executions)
ORIGINAL_PROVIDER_INVOCATIONS = 1 per turn
ORIGINAL_DURABLE_PROVIDER_STARTED = 0 per turn
FAILURE_POINT = actual foreground provider invocation, followed by interruption before a durable provider_generate marker existed
TURN_IDS = 5b67a284-c7e9-4af0-ade8-0b4e1a486491, 7cb12b2c-8828-48b6-9128-b83950d20643
POST_INTERRUPTION = turn pending; assistant_message_id null; stages empty

Both executions traversed the actual Desktop ChatTurnCoordinator.execute and TurnDurability.begin_turn with a deterministic synthetic provider observer and synthetic shared DB. Each observer read the ledger and terminated its child process immediately after provider entry. An independent DB reader confirmed the pending turn and missing stage rows. This reproduced missing durable ownership, not an Android replay. Original logs remain in the prior .2.6 evidence tree.

## [Root Cause]

FOREGROUND_PATH = React -> POST /chat -> app.routers.chat.send_chat_message -> ChatTurnCoordinator.execute -> _execute_chat_turn -> TurnDurability.begin_turn -> foreground context construction -> app.services.llm.generate_reply
PROVIDER_STARTED_WRITE = TurnDurability.mark_provider_started
PROVIDER_INVOCATION = app.services.llm.generate_reply, invoked by ChatTurnCoordinator.execute

Before the fix, begin_turn committed only the user message and pending turn. The foreground coordinator then called generate_reply after context construction without creating or advancing the manual context_prepare/provider_generate/assistant_persist rows. Those rows were used by other paths; post-cognition durability was written later by complete_core. Thus no foreground provider-start write or commit existed before provider entry. Interruption after a provider invocation left a pending turn, no assistant, and stages=[].

A. Turn row commits in the begin_turn transaction before context/provider work.
B. The marker writer is TurnDurability.mark_provider_started.
C. The actual foreground coordinator now calls it immediately before generate_reply.
D/E. The marker method exits its DB transaction before returning; a separate fresh reader observed provider_generate=running before every provider callback.
F. Interruption/error preserves provider_generate=running as ambiguous; it is not rolled back or deleted by failure handling.
G. The foreground path now uses the TurnDurability API.
H. The synthetic provider harness traverses actual ChatTurnCoordinator foreground execution; only the provider implementation is a test-only seam.

ROOT_CAUSE = foreground path omitted the durable provider_generate running transition before provider invocation.

## [Fix]

FIXED_ORDERING = begin_turn commits user + pending turn + three pending foreground stage rows -> context construction -> mark_foreground_context_prepared commits context_prepare=completed -> mark_provider_started commits provider_generate=running -> generate_reply -> complete_core atomically persists assistant, core completion, provider completion, and assistant_persist completion
PROVIDER_STARTED_COMMIT_CONFIRMED = YES
PROVIDER_CALL_BLOCKED_ON_DURABILITY_FAILURE = YES
SCHEMA_CHANGED = NO

Each writer returns after exiting its DB transaction. The accepted harness used a fresh independent DB read at the provider seam and saw provider_generate=running for all calls. Injected failure at marker persistence propagated before the provider seam: zero calls. Provider interruption leaves the running marker intact, so an ambiguous call is not replayed as never started.

## [Interruption]

CHECKPOINT_A = PASS (marker write failure boundary: provider calls 0; context completed; provider pending; assistant rows 0)
CHECKPOINT_B = PASS (10/10, committed marker then interruption before provider; zero provider calls each; fresh reader sees running marker)
CHECKPOINT_C = PASS (10/10, provider entered then child stopped before result persistence; one call each; fresh reader sees running marker)
STRESS_ITERATIONS = 20 (10 B + 10 C)
PROVIDER_INVOCATIONS = 10
PROVIDER_INVOCATIONS_WITHOUT_DURABLE_PROVIDER_STARTED = 0
MISSING_PROVIDER_STARTED_AFTER_PROVIDER_INVOCATION = 0
DUPLICATE_PROVIDER_INVOCATION_FOR_TEST_TURNS = 0
DUPLICATE_ASSISTANT_OUTPUT = 0
PROVIDER_STARTED_VISIBLE_BEFORE_PROVIDER_INVOCATION = YES
FOCUSED_FAILURES = 0
FOCUSED_ERRORS = 0
FOCUSED_CORE_SKIPS = 0

All 20 interrupted turns retained the running marker and had no assistant output. B stops before provider entry; C invokes once per logical turn.

## [Cross Device]

ANDROID_RECOVERY_CALLABLE = PASS
ANDROID_RECOVERY_ENTRY = accepted A.2.1.2.5 recovery_once via M7333A2125RecoveryHarnessTest
ANDROID_RECOVERY_READ = Desktop-origin T_D with provider_generate=running in the shared schema24 DB
ANDROID_REPLAY_OF_DESKTOP_PROVIDER = 0
DUPLICATE_ASSISTANT_OUTPUT = 0
SAME_OWNER_RECOVERY_SANITY = PASS

Android ran the actual recovery entry point against the shared DB; the result was not substituted by direct DB inspection alone. It did not invoke the provider for Desktop-origin interrupted turns. Same-owner recovery passed. Android-origin-to-Desktop replay and cross-device claim race remain unproven.

## [Tests]

FOCUSED_TESTS = 41/41 accepted checks (33 focused Desktop Python tests + 8 Android instrumentation cases)
FOCUSED_FAILURES = 0
FOCUSED_ERRORS = 0
FOCUSED_CORE_SKIPS = 0
DESKTOP_PYTHON = 643 passed, 3 skipped, 380 subtests passed; 1 Starlette deprecation warning
DESKTOP_FRONTEND = 98 passed in 17 files
DESKTOP_RUST = 60 passed
ANDROID_RELEVANT_TESTS = Python 102 passed; JVM 9 passed/0 skipped; instrumentation 8 passed/0 skipped
NEW_REGRESSIONS = 0

Three Desktop skips are disposable remote Turso tests gated by absent MINDCORE_TEST_TURSO_URL and MINDCORE_TEST_TURSO_TOKEN. No values were read. They are secret/environment-gated, not code failures. An initial Android JVM command used the wrong Gradle root; rerunning from android/ passed. A first Android debug test APK build found a test-only type reference typo, corrected before the passing rebuild and instrumentation run.

## [Build]

DESKTOP_FRONTEND_BUILD = PASS
DESKTOP_SIDECAR_BUILD = PASS
DESKTOP_TAURI_BUILD = PASS
ANDROID_RELEASE_BUILD = PASS (fresh unsigned APK; 21,485,786 bytes; arm64-v8a; 16 KB alignment PASS)
ANDROID_RELEASE_DEBUGGABLE = NO
ANDROID_RELEASE_CLEAR_TEXT = NO

Desktop sidecar: 27,806,608 bytes, SHA-256 ea2083ab1f298cc7bafe5067585b7025f994d33ccbee5e00129a73172cebcaed. Final MindCore.app: 47,299,620 bytes. Android APK SHA-256 b6750d9f7274468db4eb7914471e70aae56ee4df0754101749c5644ebdbf1227. Release artifact scans found no test marker, checkpoint, synthetic control, or test DB locator. pytest was temporarily absent only while packaging the final Desktop sidecar; the exact pytest version was reinstalled and verified afterward.

## [Security]

CONFIRMED_REAL_SECRETS_DESKTOP = 0
CONFIRMED_REAL_SECRETS_ANDROID = 0
UNRESOLVED_SECRET_SCAN_MATCHES = 0
FAILURE_INJECTION_IN_RELEASE = NO
SYNTHETIC_PROVIDER_CONTROL_IN_RELEASE = NO
PRODUCTION_SECURITY_WEAKENED = NO

Changed source/test/debug and evidence text scope (228 text files) was scanned for GitHub/provider key formats, JWTs, AWS access-key IDs, and private-key blocks without printing values; detector counts were zero for all patterns. Harness synthetic identifiers are test data, not credentials. Release marker scans were clean. No production credentials, private Persona data, updater signing secret, or permanent Android signing key was accessed.

## [Capabilities]

SHARED_PERSONA_DB = TRUE
PERSONA_SHARED_RUNTIME = TRUE
CONCURRENT_SHARED_WRITES = TRUE
PROVIDER_STARTED_DURABILITY = TRUE
DESKTOP_PROVIDER_START_BOUNDARY_SAFE = TRUE
DESKTOP_TO_ANDROID_PROVIDER_REPLAY_SAFE = TRUE
CROSS_DEVICE_PROVIDER_OWNERSHIP = PARTIAL
POST_COGNITION_EXACTLY_ONCE = NOT_PROVEN
SHARED_RUNTIME_SAFETY = PARTIAL
FULL_COGNITION = TRUE (existing capability; not re-proven here)
AUTHENTICATED_REMOTE_DB = NOT_PROVEN

The architecture remains LOCAL_ONLY_PERSONA with one-device authoritative DB and SHARED_PERSONA with one shared authoritative DB. Historical peer/replica synchronization was not used or expanded. This hotfix does not promote .2.6 to PASS.

## [Commits]

DESKTOP_IMPLEMENTATION_COMMIT = NONE
DESKTOP_REPORT_COMMIT = NONE
ANDROID_IMPLEMENTATION_COMMIT = NONE
ANDROID_REPORT_COMMIT = NONE
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO

## [Next]

NEXT_DECISION = RESUME_M7_3_3_A_2_1_2_6

Continue with Android-origin provider-start to Desktop replay=0, Android outage safety, completed cognition rerun prevention in both directions, and cross-device stage-claim race evidence. Reuse accepted Desktop-origin evidence above. Do not infer full shared-runtime safety from this hotfix.
