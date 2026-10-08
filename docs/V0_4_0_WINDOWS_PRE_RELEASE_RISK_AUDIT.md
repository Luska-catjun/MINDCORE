> **Current WRA-01/02 closure:** Local acceptance PASS; Windows signed CI pending; RELEASE_READY=FALSE. See the final closure section. Earlier audit/readiness statements below are historical evidence.

# MindCore 0.4.0 Windows Pre-Release Risk Audit

Audit date: 2026-10-08 (Asia/Seoul). **AUDIT ONLY.** Android excluded.

## Decision

**WINDOWS_RELEASE_RISK = HIGH**

**WINDOWS_RELEASE_BLOCKERS = 2**: WRA-01 and WRA-02 (P1). P0 = 0, P1 = 2, P2 = 5, P3 = 1.

The existing signed Windows pipeline passed. This audit nevertheless found deterministic native Setup integration failures that the current unit/mocked frontend suites do not execute. Existing configured installations are not asserted to fail universally. A newly configured installation and a DB-token replacement have concrete failure paths. No correction was made.

IMPLEMENTED != ACCEPTANCE-PROVEN. Successful compilation, signing and helper tests do not prove first-run onboarding.

## Scope and source identity

- Repository: `/Users/noseunghudong-alibujang/Developer/mindcore-desktop`
- Branch: `codex/v0.4.0-readiness`
- SOURCE_HEAD: `15d16ef0281521c03234e939586110e9e8092f4e`
- Windows target: `x86_64-pc-windows-msvc`, Desktop 0.4.0, NSIS.
- The request's `c27d9d0d2567db40c16be8c597be6f6fe75d0e48` is an earlier ancestor. Current public production verification branch resolves to 15d16ef. Local origin remains the private backup URL; that origin did not advertise this verification branch in the read-only query. No remote was changed.
- Actual successful signed artifact source: `20275953e96bc438eb9bec38f080903c73dee100`. Only the previous readiness report differs between this SHA and SOURCE_HEAD. Runtime/source/workflow are identical.
- Working tree was clean on entry. This new report is the sole Git-visible audit change; ignored evidence/build output is under `.toolchain/pre-release-risk-audit/`.
- No product/test/workflow/lockfile edits, new skips, assertions changes, migrations, dependency upgrades, commit, push, tag or release. Sevenzip 26.03 was installed as an external inspection tool, not a project dependency.
- No real Persona DB, personal configuration, OS credential contents or private signing material was read or changed. Synthetic diagnostic values only. No feedback email was sent.
- LOCAL_ONLY remains one-device authoritative; SHARED remains one authoritative DB accessed directly. Legacy peer/replica/manual-sync code was inspected for reachable platform/lifetime risks only. No new sync foundation is proposed.

## Regression executed during this audit

Evidence root: `.toolchain/pre-release-risk-audit/`. All commands bounded by runner timeouts; none timed out.

| Command | Current macOS comparison result | Evidence |
|---|---|---|
| `.venv/bin/python -m pytest -q` | **657 passed / 1 skipped / 380 subtests passed**, 62.69s | `python.log` |
| `.venv/bin/python -m compileall -q app desktop` | PASS | `python-compile.log` |
| `npm test` | **27 files / 162 passed** | `frontend.log` |
| `npm run lint` | PASS; 6 existing no-useless-escape warnings | `lint.log` |
| `npm run build` | PASS; ineffective dynamic import warning | `frontend-build.log` |
| `npm run test:updater-config` | 3 PASS | `updater-config.log` |
| `npm run test:release-notes` | 1 PASS | `release-notes.log` |
| `cargo test --locked` | **64 passed / 0 failed / 0 ignored** | `rust.log` |
| `cargo check --locked` | PASS (host target) | `cargo-check.log` |

Python 3.12 virtualenv; frontend Node 24 from the existing local toolchain. Real native sqld fixture explicitly supplied through MINDCORE_TEST_SQLD. The supported-platform lifecycle test ran in the full local suite. The one Python skip is the disposable remote-Turso test without test credentials. Pytest counts include pytest-only tests; Windows discovery uses unittest and is not expected to have the same total. No numbers were adjusted to match.

### Windows CI evidence

Read-only rechecked [Windows run 37723781839, attempt 2](https://github.com/Luska-catjun/MINDCORE/actions/runs/37723781839/attempts/2): SUCCESS, source 2027595. Existing `signed_build_only=true`, `build_only=true` path.

- Python unittest: 648 discovered, **639 PASS / 9 SKIP / 0 FAIL / 0 ERROR**, 201.324s.
- Frontend 162 PASS; lint/build PASS; Rust 64 PASS; Windows cargo check PASS.
- Native Windows sidecar build and archive boundary PASS.
- Production signing configuration, signed NSIS, nonempty updater signature and artifact upload PASS.
- GitHub Release publishing was skipped by the existing dispatch condition. No tag/release created in this audit.
- Nine skips: remote credentials absent (1), released v0.2.0 fixture absent in shallow checkout (2), POSIX mode bits not Windows ACL evidence (5), unsupported real native sqld lifecycle (1). None is a demonstrated production defect.
- c27d9d0 excludes only that lifecycle case on win32. Native sqld 0.24.32 has no supported native Windows binary/target. Other three harness primitives remain portable; Mac real lifecycle remains exercised. No Windows sqld was created, ported, emulated or provisioned.
- Attempt 1 on the SAME 2027595 source exited Python with no traceback/unittest failure summary near a schema structural-drift case. Exact cause remains **UNKNOWN**. Full local suite and Windows attempt 2 pass; there is no concrete production crash attribution. Preserve this as an evidence limitation, not an invented P0/P1 finding. Earlier CRLF pretest failure was already fixed by `.gitattributes` and is not a current finding.

CI logs/metadata: existing `.toolchain/windows-platform-gate/ci-final.log`, `ci-final.json`, `python-abrupt-exit.json`; current read-only API snapshot: `windows-ci-current.json`.

## Actual installer inspection

Inspected the uploaded installer, not just the build directory:

- `MindCore_0.4.0_x64-setup.exe`, 30,439,335 bytes.
- SHA256: `065f7830f448e6154a64d049a4dbfb5cf8bff619d6c5b052ec48731b7e712874`.
- Sevenzip extraction finds **mindcore.exe** (16,034,816 bytes) and **mindcore-backend.exe** (26,944,667 bytes), uninstall executable and NSIS plugins.
- Final sidecar CArchive: 1,000 members; PYZ: 1,171 modules. Python312.dll, VCRUNTIME140/140_1, UCRT, SQLite/OpenSSL DLLs, libsql cp312-win_amd64, asyncpg native modules, cryptography, keyring Windows backend and win32 credential adapter, HTTP stack, certifi CA bundle, tzdata, schema baseline, generic identity/memory prompts and skills are present.
- **95 app modules plus sidecar entrypoint** match current Python source semantically: bytecode/arguments/names/variables/closures/exception table/constants compared, filenames/line metadata excluded. Zero missing/mismatching app modules. Frozensets compared as sets, not iteration order. Evidence: `packaged-source-semantic-comparison.json`.
- Native executable contains the production GitHub latest.json endpoint and sidecar runtime name; development `.invalid` endpoint absent; UTF-16 version 0.4.0 present. Frontend assets are embedded by Tauri; they need not appear as loose installer files.
- Repeated minisign verification against the existing production PUBLIC verification key: **Signature and comment signature verified**. This is updater-signature evidence, not an Authenticode/SmartScreen certificate claim.
- Archive names contain no `.env`, updater private key or client-secret asset. Pattern scan of 168 production text files and embedded app-code constants: zero Gmail recipients, Google OAuth token patterns, GitHub token patterns or private-key blocks. This is bounded pattern/data-flow evidence, not proof that every possible opaque secret is absent. Dependencies may contain generic security terminology; no private material was exported.
- No Windows machine/VM is available here. Installed Windows first launch, Windows Credential Manager behavior, Unicode/long user-path QA and A→B installer relaunch were **NOT RUN** in this audit. Existing CI compilation and artifact verification do not replace those runtime checks.

Evidence: `nsis-inventory.txt`, `nsis-extract.log`, `sidecar-members.json`, `sidecar-python-modules.json`, `installer-inspection.json`, `native-installer-constants.json`, `extracted-sidecar-boundary.log`, `updater-signature.log`, `source-privacy-scan.json`.

## Findings

### WRA-01 — LLM preflight cannot reach the provider from native Setup

- **ID:** WRA-01
- **Severity:** P1
- **Category:** RELEASE BLOCKER
- **File / Symbol:** `frontend/src-tauri/src/main.rs:590` preflight_env_for_action; `:746` setup_action; `:329` profile_credential_id. `frontend/src/components/SetupWizard.tsx:33,66`.
- **Windows-specific:** No; the same native commands are in the Windows binary.
- **Trigger:** Fresh Setup reaches Language Model → Test Connection, or an existing installation changes provider/key/model and requires revalidation.
- **Execution path:** LLM staging deliberately retains selected-provider keys and strips DATABASE_CREDENTIAL_ID → setup_action unconditionally calls profile_credential_id on that staging file → missing-reference Err propagates before sidecar/provider execution → wizard marks llmValidation invalid and disables Continue.
- **Impact:** Valid provider credentials cannot complete normal first-run onboarding. UI incorrectly attributes this local integration failure to API key/model connectivity. Existing installations not entering validation are unaffected by this path.
- **Evidence:** Executed byte-for-byte extracted production helper bodies in an ignored diagnostic binary; database staging lookup succeeds, LLM staging lookup returns `The Persona database credential reference is missing.` No live provider or credentials needed. `registry-probe.log`, `preflight-probe-source-hashes.json`. Direct source trace proves the unconditional call and UI gate.
- **Existing test coverage:** Rust tests check staging isolation; frontend SetupWizard tests mock native invoke. Neither composes staging with setup_action's required credential lookup. Full tests therefore still pass.
- **Confidence:** HIGH; deterministic local helper failure and explicit UI gating. Actual installed Windows click-through not run.
- **Release blocker:** YES.
- **Recommended next action:** After user selects fixes, correct the action-specific credential boundary and exercise the real native LLM preflight. Preserve provider isolation and its assertions. No updater/DB architecture redesign is needed.

### WRA-02 — First profile creation/token replacement violates its own credential invariant

- **ID:** WRA-02
- **Severity:** P1
- **Category:** RELEASE BLOCKER
- **File / Symbol:** `frontend/src-tauri/src/main.rs:524` draft_env_for_persona_name; `:336` migrate_profile_database_credential; `:1191` persist_active_draft; `:1710` save_mindcore_config_inner. `frontend/src-tauri/src/persona_registry.rs:445` create_initial_registry_with; `:239` profile_config_text.
- **Windows-specific:** No; Windows production uses these same registry/credential commands.
- **Trigger:** First successful save of a new Persona, or reconfiguration supplying a replacement DB token (`preserve_database_auth_token=false`). WRA-01 masks first-run progression but this is an independent failure after that stage is reached, and token replacement is reachable on an already configured installation.
- **Execution path:** draft_env allocates/stores token under fresh credential ID A → initial-registry creation allocates separate Persona ID B and preserves credential reference A → next registry() calls migrate_profile_database_credential → with no plaintext token it requires reference == profile.persona_id → A != B returns invalid-reference error. Replacement-token update similarly gives an existing profile a new reference instead of its Persona ID.
- **Impact:** Configuration can be saved and then immediately become unloadable; backend start/restart fails and regular Persona/config commands fail. DB contents are not deleted; the referenced token may still exist in the OS store. New token rotation can strand an existing installation until its configuration is repaired.
- **Evidence:** Actual create_initial_registry_with/profile_config_text invoked in isolated temp directories with the shape produced by draft_env: `credential_reference_matches_profile_id=false` for both created profiles. Source shows the next-load equality rejection. `registry-probe.log`. No OS keyring write or real config modification was performed by the diagnostic.
- **Existing test coverage:** Initial registry persistence and credential store primitives are covered separately; they do not traverse production draft-generated credential reference → first registry → migration invariant. Portable Windows suite also does not run native first-run Setup.
- **Confidence:** HIGH. Actual initial registry functions executed; token-rotation rejection traced statically through the same invariant. Windows OS-store behavior not asserted.
- **Release blocker:** YES.
- **Recommended next action:** Select a narrow correction to make generated credential references and the loader invariant consistent, retaining OS-native secret storage and fail-closed validation. Prove first save/restart and token replacement/restart with synthetic credentials before release.

### WRA-03 — Fresh registration cannot attach to an already bound shared Persona

- **ID:** WRA-03
- **Severity:** P2
- **Category:** RELEASE RISK
- **File / Symbol:** `frontend/src-tauri/src/persona_registry.rs:445,492,716` initial/add/legacy registration; `app/desktop_backend.py:285` _setup_action; `app/database/persona_storage.py:55` validate_for_protected_action.
- **Windows-specific:** No; Windows fresh/rebuilt configuration reaches it.
- **Trigger:** A user configures the URL/token of an existing authoritative SHARED Persona through fresh Setup/add-Persona or migrates a standalone legacy configuration, without an existing matching registry identity.
- **Execution path:** Connectivity/schema preflight does not return/adopt authority identity; registration generates a new UUID (legacy migration also does not retain source PERSONA_ID) → authority already owns a different stable ID → auth/status reports BOUND_MISMATCH and protected writes/chat are rejected.
- **Impact:** Existing shared continuity cannot be established through these registration paths alone. Existing matching registry installations and same-ID recovery harnesses remain valid. No cross-Persona write is permitted: the guard itself behaves correctly.
- **Evidence:** Two real registry creations with the same synthetic DB configuration yielded distinct IDs; real binding functions on isolated synthetic SQLite returned first BOUND_NOW, second BOUND_MISMATCH; original owner unchanged. `registry-probe.log`, `binding-probe.json`. This diagnostic is not remote-Turso authentication or Windows runtime E2E evidence.
- **Existing test coverage:** Binding/concurrency/isolation tests and accepted recovery harness explicitly supply matching Persona IDs; they prove safety, not fresh-client registration into an existing authority.
- **Confidence:** HIGH for the bounded registration mismatch; no claim that all current users hit it. P2 because it requires existing authority plus missing local identity, not a new empty authority or preserved registry.
- **Release blocker:** NO in this classification; independently confirm supported existing-Persona attach/recovery expectations.
- **Recommended next action:** Decide the supported attach/recovery behavior and prove it with one stable authority ID. Preserve mismatch rejection; do not rewrite historical rows, create replicas or relax binding assertions.

### WRA-04 — LOCAL_ONLY classification is unreachable through the production pool factory

- **ID:** WRA-04
- **Severity:** P2
- **Category:** RELEASE RISK
- **File / Symbol:** `app/database/persona_storage.py:18` storage_mode; `app/database/connection.py:104` create_pool; `app/main.py:88` lifespan pool creation; native profile_overrides.
- **Windows-specific:** No; applies to a Windows installation carrying a file: Persona profile.
- **Trigger:** Existing/externally configured authoritative LOCAL_ONLY Persona URL uses file: and has the required credential reference/token. Normal current Setup advertises Turso fields; this does not assert an ordinary fresh shared install fails here.
- **Execution path:** file: classifies LOCAL_ONLY in the mode helper → create_pool rejects every scheme except https/libsql before lifespan reaches mode selection or its availability catch → setup/start fails.
- **Impact:** The declared LOCAL_ONLY mode cannot start via the normal Desktop pool factory. Direct TursoPool file fixtures do not prove this production factory works. This is not a requirement to ship native sqld on Windows.
- **Evidence:** Actual create_pool invoked on a synthetic file: URL returns `shared_database_https_required`; `binding-probe.json`; static creation order in main.py.
- **Existing test coverage:** Direct adapter/file binding and legacy manual-sync tests exercise local storage below the factory; they do not cover this production route.
- **Confidence:** HIGH for rejection; current prevalence of such profiles UNKNOWN.
- **Release blocker:** NO for the current shared-Turso installer path; scope/capability limitation must be explicit.
- **Recommended next action:** Clarify which LOCAL_ONLY entry path 0.4.0 supports and validate that exact path. Keep strict HTTPS on SHARED connections and the one-device authoritative distinction.

### WRA-05 — Per-Persona proactive policy is discarded and never injected into the backend

- **ID:** WRA-05
- **Severity:** P2
- **Category:** RELEASE RISK
- **File / Symbol:** `frontend/src-tauri/src/main.rs:1412` update_proactive_settings; `:336` migrate_profile_database_credential; `:971` start_sidecar. `frontend/src-tauri/src/persona_registry.rs:239,882` profile_config_text/profile_overrides.
- **Windows-specific:** No; the Windows settings surface uses this same persistence path.
- **Trigger:** Save enabled/cooldown/quiet-hours settings for a Persona, then active restart or any registry read.
- **Execution path:** update writes PROACTIVE_* to persona.env → start_sidecar/config_is_complete or list_personas calls registry migration → profile_config_text reprojects only fixed DB/identity keys, dropping PROACTIVE_* → profile_overrides also excludes those keys → Settings receives global/default policy rather than saved per-Persona policy.
- **Impact:** UI can report saved while restart restores defaults or global values. The optional feature cannot rely on its displayed per-Persona policy. SHARED scheduler being intentionally disabled is a separate restriction and does not explain lost persistence.
- **Evidence:** Actual profile projection and overrides run with PROACTIVE_ENABLED=true/cooldown3600: both omit these keys (`registry-probe.log`). Source trace includes the save→registry read→rewrite chain.
- **Existing test coverage:** Rust policy map round-trip and mocked UI save/remount tests pass without applying registry reprojection/runtime overrides.
- **Confidence:** HIGH for key loss and omitted injection.
- **Release blocker:** NO; optional feature, safe disabled default, no demonstrated DB damage.
- **Recommended next action:** Validate the complete per-profile save→registry reload→backend Settings chain, preserving safe defaults and Shared runtime safety boundaries.

### WRA-06 — Committed correction reported as “Nothing was changed” after read failure

- **ID:** WRA-06
- **Severity:** P2
- **Category:** RELEASE RISK
- **File / Symbol:** `frontend/src/components/WorkspacePanel.tsx:32` CorrectionControls.run and its memory/preference/narrative/self-model/knowledge callers.
- **Windows-specific:** No; same product UI on Windows.
- **Trigger:** PATCH/DELETE completes successfully but the subsequent refresh GET fails or loses connectivity.
- **Execution path:** await action() commits → await refresh() rejects → one catch labels both operations `Could not update this item. Nothing was changed.` → offers mutation retry while old items/dialog remain.
- **Impact:** User receives an incorrect outcome for a real durable change and can repeat an already applied correction/deletion. No automatic rollback exists in this frontend path. Transport-ambiguous mutation failures further require care but are not claimed to prove an additional server defect.
- **Evidence:** Direct sequential await/catch trace. Existing routes mutate before the follow-up GET; GET failure cannot undo that completed transaction. No real user mutation performed in audit.
- **Existing test coverage:** Correction success and mutation rejection covered; successful mutation followed by failed refresh not covered.
- **Confidence:** HIGH for the stated success-then-read-failure trigger.
- **Release blocker:** NO; bounded network failure/incorrect presentation, not proven general data loss.
- **Recommended next action:** After selection, separate durable mutation outcome from refresh outcome; prove success-then-refresh-failure behavior before inviting a second mutation.

### WRA-07 — Data refresh/sort has remaining unguarded result ordering

- **ID:** WRA-07
- **Severity:** P2
- **Category:** RELEASE RISK
- **File / Symbol:** `frontend/src/components/WorkspacePanel.tsx:34` MemoryWorkspace.changeSort; `:156` WorkspacePanel.refresh.
- **Windows-specific:** No; Windows fetch responses can complete out of order like other platforms.
- **Trigger:** Rapid memory sort clicks with delayed/reordered responses; or correction refresh pending while navigating to another Data view.
- **Execution path:** Sort buttons stay enabled; each response unconditionally replaces items although selected sort can be newer. changeSort uses finally without catch and caller discards the promise. Separately, old refresh can overwrite resource with its previous view after the new view's guarded effect already completed; view matching then renders loading with no further request triggered.
- **Impact:** Results can disagree with chosen sort; sort network failure becomes an unhandled rejection without a product error; a completed newer Data view can be replaced by a persistent loading state until navigation/refetch. This is not evidence of cross-Persona DB writes/leaks: WorkspacePanel is keyed by active Persona and main-chat guards exist.
- **Evidence:** Explicit unconditional state setters and deferred completion ordering; source-path audit. These supplemental UI orderings were not executed in an installed Windows runtime.
- **Existing test coverage:** Correction/view initial fetch covered; no overlapping sort or old manual-refresh/new-view completion permutation identified.
- **Confidence:** HIGH for sort result order; MEDIUM-HIGH for the narrower old-refresh navigation sequence.
- **Release blocker:** NO; recoverable Data presentation path.
- **Recommended next action:** Reuse an appropriate request-generation boundary after selection; preserve existing initial-view and chat revision/generation guards.

### WRA-08 — Production chat debug instrumentation remains enabled

- **ID:** WRA-08
- **Severity:** P3
- **Category:** TECH DEBT
- **File / Symbol:** `frontend/src/chatDebug.ts:30,68` chatDebug; App/ChatWindow callers.
- **Windows-specific:** No.
- **Trigger:** Any production chat cache/render/send/fetch update.
- **Execution path:** No DEV guard → window.__DIANA_CHAT_DEBUG__ and console.debug receive conversation/message IDs, sequences, revisions and counts.
- **Impact:** Unnecessary production diagnostic metadata/work remains. Current summary explicitly excludes message content, tokens and API keys; no demonstrated credential/content leak or unprivileged remote reader.
- **Evidence:** Summarize maps id/sequence only; source and generated production JS inspection. Backend child forwarding similarly allowlists fixed timing records.
- **Existing test coverage:** Chat regression uses this metadata seam; it is not a release-vs-debug gating test.
- **Confidence:** HIGH.
- **Release blocker:** NO.
- **Recommended next action:** Decide whether this diagnostic seam should ship or be gated in a later selected cleanup; no emergency security assertion is justified by this metadata alone.

## Coverage and exclusions by execution path

| Area | Trace / result |
|---|---|
| Windows assumptions | Production source inventory/path/exception searches retained. No reachable hardcoded /Users or /tmp installation path, bash/sh invocation, fork, Unix socket or shell=True identified in normal startup. PathBuf/Path, argv arrays and target-aware .exe/semicolon handling used. Unix permission calls guarded. Developer/test-only paths separated. Unicode/MAX_PATH/AV ACL interaction not runtime-tested; do not infer PASS. |
| NSIS/startup/sidecar | externalBin target suffix maps source mindcore-backend-x86_64-pc-windows-msvc.exe to installed mindcore-backend.exe; confirmed actual installer. Args/environment convey config/profile/parent PID, not shell strings. _MEIPASS resolves sidecar resources independently of Program Files CWD. Loopback fixed port refuses occupancy rather than attaching an unrelated process. Readiness checks secret instance capability after lifespan, bounded 45s after spawn. Startup integration risks WRA-01/02 remain. |
| SQLite/file handles | 3da63a0 closes raw initializer on BaseException before corruption archival. _open closes assigned connection before rename/rebuild; normal context closes store. Persona file operations have explicit close paths. libSQL adapter closes consumed cursors; _run_blocking shields in-flight statement calls before rollback/close; acquire scopes own connections. Current local and Windows regression show no remaining demonstrated WinError32 failure. No fault-injection claim for every OS/filesystem/OOM failure or Windows installed handle lifecycle. |
| User data/config | Tauri app_config_dir under the app identifier; mindcore.env/personas.json/per-profile persona.env/identity paths. No macOS Library path leaks into release defaults. NamedTempFile persists after flush/sync; Unix mode code cfg-gated. Credential Manager/keyring is native fail-closed, no plaintext DB-token fallback. Corrupt registry returns explicit error; missing config reaches Setup. Multi-file rollback is best effort and must not be advertised as crash-atomic across files. WRA-02/05 are concrete persistence risks. |
| Persona/shared | Matching stable IDs guarded per protected write; network/auth/mismatch errors reject rather than create a local fallback. Runtime generation/history/snapshot scoping covered by existing tests. WRA-03/04 bound capability gaps remain. Legacy replica transport absent from normal navigation and not proposed for continuity. |
| Chat/messages/labels | resolver maps diana/user at render time to configured labels, uses Persona/User abnormal fallback, unknown roles → Message. Chat, data Messages and delete dialog audited. Active registry display name/auth me user_display_name feed props; Persona switch clears cache and generation, panel keyed by Persona. Historical DB rows/payload roles unchanged. Tests cover synthetic names, switch and role immutability. No visible raw role label found in these renderers. |
| Frontend async | Main conversation, send settlement and ChatWindow fetch cancellation/revision guards recognized; no duplicate-send or cross-conversation defect attributed merely to async code. Some reconnect/metadata/proactive paths lack complete generation checks, but without a demonstrated reachable harm they are not extra findings. Concrete Data ordering issue WRA-07 only. |
| Updater/shutdown | Signature verification belongs to existing Tauri updater. JS downloads before stop, native authenticated shutdown waits then kill fallback; installer failure before handoff tries backend restart. Inspected installed tauri-plugin-updater 2.11.0 Windows source: ShellExecuteW of NSIS /UPDATE, plugin exits parent after successful handoff, installer owns restart; JS relaunch is not expected to complete on Windows success. No architecture defect inferred from this. Actual A→B update/locked-file behavior NOT RUN. |
| Feedback | Accepted public Workers HTTPS endpoint; POST JSON contract, 15s AbortSignal, redirects rejected, omitted credentials, bounded strictly validated JSON acknowledgement. Failure preserves input/retry; configured endpoint failure does not silently publish to GitHub. Unconfigured endpoint opens explicit GitHub compose. This preserves the accepted post-endpoint contract; recipient/OAuth secrets never in client data flow. Windows WebView2 end-to-end network path not newly executed; previous Gmail E2E receipt evidence is historical only. |
| Notifications | Native macOS fixed x-apple settings URL vs Windows ms-settings:notifications; UI says operating system, not macOS. Permission errors fall back to in-app notification without fatal errors. Delivery in installed Windows NOT RUN. WRA-05 policy persistence is separate. |
| Packaging/imports | Actual DLL/package/prompt/schema/timezone/cert resources present, provider adapters statically discoverable in PYZ; no missing app modules. Native build/signature validated. WebView2 is handled by standard Tauri NSIS behavior, not a loose Python dependency; offline fresh-machine bootstrap/native certificate/antivirus behavior NOT RUN. No arbitrary missing-DLL blocker invented. |
| Security | Loopback-only service; destructive API routes pass auth middleware; lifecycle routes require per-instance capability. Browser CORS includes Windows http://tauri.localhost. Configured-provider keys remain user configuration, DB token OS store; logging paths use error type/allowlisted timings, not secret values. Read-only health/auth status and authenticated observation debug are not unauthenticated destructive routes. Pattern/archive scans have stated limits. WRA-08 is metadata debt, not secret exposure. |
| Silent exceptions | Startup native .setup intentionally ignores start error but frontend retries/statuses it. Optional cognitive hydration/enrichment failures log safe type and preserve durable turn boundaries; no new correctness bug inferred from broad catches alone. Registry rollback/restart may fail under I/O failure and should not be called guaranteed rollback. Concrete false correction outcome in WRA-06; no speculation-based additional blockers. |

## Recent commit audit

| Commit | Behavior / Windows impact / rollback decision |
|---|---|
| 801288eb40403e5e443189eeb9e4be591c66f303 | Presentation role resolver/prop wiring and production feedback URL; feedback fixtures isolated. No schema/API/provider role rewrite. Synthetic label/switch/historical-row and endpoint/error tests pass. No current regression attributed to this commit; rollback not indicated by this audit. |
| 3da63a0d2adc3032ec15acd28951f4e7817bcd69 | Runtime exception-path SQLite initializer close, plus handle-lifetime regression coverage. Windows corruption/recovery locking error fixed in subsequent CI; current full suite passes. No schema/sync algorithm expansion. No current regression attributed; preserve fix. |
| c27d9d0d2567db40c16be8c597be6f6fe75d0e48 | Test-only win32 boundary on ONE unsupported real-sqld lifecycle case; method assertions/body preserved, other primitives remain running. macOS test executes with real sqld. No product/updater behavior change; preserve boundary, no Windows sqld provisioning. |
| 20275953e96bc438eb9bec38f080903c73dee100 | Release-note LF checkout attribute, existing exact heading check retained. Passed Windows pretest/build; not a current risk finding. |
| 15d16ef0281521c03234e939586110e9e8092f4e | Previous readiness report only. Audit source, product identical to signed artifact source. |

The P1 findings are composed native Setup defects at current HEAD; they are not attributed to the three recent commits simply because those commits are recent.

## Final audit summary

[Audit Scope]

SOURCE_HEAD = 15d16ef0281521c03234e939586110e9e8092f4e

WINDOWS_TARGET = x86_64-pc-windows-msvc / Desktop 0.4.0 / NSIS

PRODUCT_CODE_CHANGED = NO

[Regression]

PYTHON = PASS, 657 passed / 1 skipped / 380 subtests passed

FRONTEND = PASS, 162 tests; lint PASS with 6 warnings

RUST = PASS, 64 tests; cargo check PASS

BUILD = frontend production PASS; existing signed Windows NSIS PASS; extracted sidecar/dependencies and updater signature PASS

WINDOWS_CI_EVIDENCE = 37723781839 attempt 2 SUCCESS, source 2027595; actual installed Windows UI/update E2E NOT RUN

[Findings]

P0 = 0

P1 = 2 (WRA-01, WRA-02)

P2 = 5 (WRA-03 through WRA-07)

P3 = 1 (WRA-08)

[Recent Changes]

801288e = PASS, presentation/feedback changes; no current regression attributed

3da63a0 = PASS, initializer handle close retained

c27d9d0 = PASS, supported-platform test boundary retained

[Windows Release Risk]

STARTUP = HIGH, WRA-01/02 normal fresh Setup/token reconfiguration blockers

SIDECAR = build/resource/readiness boundary PASS; installed Windows lifecycle NOT RUN

SQLITE_FILE_LOCKING = no current demonstrated locking blocker; Windows regression PASS

PERSONA = HIGH WRA-02; bounded shared attach/local-only/policy risks WRA-03/04/05

CHAT = label/role and current regression PASS; usable fresh-install chat blocked by Setup defects

UPDATER = signed NSIS and cryptographic signature PASS; installed A→B runtime NOT RUN

FEEDBACK = current contract/tests/secret boundary PASS; Windows UI E2E not newly run

PACKAGING = actual installer/sidecar/import resources/signature PASS; first-run acceptance incomplete

USER_DATA = WRA-02/05 config correctness; no demonstrated DB deletion or row rewrite

SECURITY = no immediate bundled-secret/unauthenticated destructive-path blocker found; bounded scan limitations and WRA-08 metadata debt

[Final Decision]

WINDOWS_RELEASE_RISK = HIGH

WINDOWS_RELEASE_BLOCKERS = 2

Blockers: WRA-01 native LLM preflight missing credential reference; WRA-02 generated credential reference rejected by subsequent registry loading.

**STOP. Findings left unfixed. User chooses any subsequent fixes.**

## WRA-01 / WRA-02 blocker closure — 2026-10-08

This section supersedes the original WRA-01/02 disposition. The original audit findings and evidence remain above as the pre-fix record. WRA-03 through WRA-08 remain unchanged.

### WRA-01 closure

- ROOT_CAUSE_CONFIRMED = YES. LLM staging deliberately omitted the DB credential reference, but native setup performed unconditional DB lookup before calling the provider.
- FIX_COMMIT = `THIS_VERIFICATION_COMMIT (SHA recorded after commit)`.
- FIX = action-specific credential boundary: llm reads only global selected-provider settings and never loads/migrates a Persona or resolves a DB credential; database/classify/initialize still require a real reference and token. New/replacement DB tokens remain in memory until final save; preserved tokens use the existing native secure-store lookup. No dummy credential/reference or plaintext-token fallback.
- TARGETED_TESTS = PASS: LLM reaches real Python `_setup_action` and real Gemini key/request handling without DB reference/lookup; missing provider key/preserved key fails closed; other provider preserved-key absence does not block selected-provider-only preflight; missing DB reference/token/store entry prevents execution; synthetic valid DB SELECT 1 succeeds; real Gemini 404 classification remains model_or_api_version, not a DB failure.
- NATIVE_SETUP_ACCEPTANCE = PASS (isolated production-helper command composition).
- TOKEN_REPLACEMENT_ACCEPTANCE = PASS.
- WINDOWS_CI = PENDING — existing Windows Release, build_only=true, signed_build_only=true.
- RESIDUAL_RISK = installed Windows UI and actual Windows Credential Manager integration NOT_RUN; synthetic adapters cover external I/O only. No live provider request was required or sent.

### WRA-02 closure

- ROOT_CAUSE_CONFIRMED = YES. Draft generation stored under random credential ID A, while initial registry generated Persona ID B; replacement generated another A. Loader correctly rejected A != B.
- FIX_COMMIT = `THIS_VERIFICATION_COMMIT (SHA recorded after commit)`.
- FIX = final draft decides stable Persona ID before credential persistence; first registry receives that same ID explicitly. Existing token replacement updates the existing Persona ID slot, never creates a new identity/reference or deletes the working slot after success. Loader reference == Persona ID rule unchanged. Obsolete second draft/save allocation path removed.
- Native integration also exposed Settings extra_forbidden for DATABASE_CREDENTIAL_ID in the real staged config. Python now accepts exactly that native metadata field, excludes it from repr/model_dump, and never treats it as a token/resolver. Unknown settings still fail closed. No broad extra=ignore change.
- Failure handling = snapshot setup/config/registry/profile/identity files and previous credential before writes; credential-store failure prevents file success; file/registry failure restores prior files and token or removes the fresh slot; unsuccessful rollback returns an explicit error. This is bounded setup rollback, not a crash-proof multi-resource transaction or new persistence architecture.
- TARGETED_TESTS = PASS: new Persona/reference equality; actual save/load/native credential migration; recreated registry/restart; canonical token lookup; backend-start eligibility; replacement identity/reference unchanged; DB fixture bytes unchanged; injected credential/profile/registry/global save errors preserve existing files and credential; fresh registry failure removes new credential/config; injected restore failure reports incomplete rollback; invalid historical mismatch remains rejected before credential I/O; initial registry rejects conflicting reference.
- NATIVE_SETUP_ACCEPTANCE = PASS.
- TOKEN_REPLACEMENT_ACCEPTANCE = PASS.
- WINDOWS_CI = PENDING — existing Windows Release, build_only=true, signed_build_only=true.
- RESIDUAL_RISK = sudden process/power loss across secure-store/files is not proven atomic. If OS/filesystem rollback itself fails, explicit failure is returned rather than claiming restoration. Existing already-mismatched historical configurations remain rejected; no row rewrite or permissive migration was added.

### Native first-run and replacement evidence

`frontend/src-tauri/src/setup_acceptance_tests.rs` composes production draft/provider preparation, preflight staging, action execution, canonical save, initial/profile persistence, registry loading, credential migration and backend eligibility helpers used by native commands. Thin Tauri AppHandle/sidecar-launch adapters are replaced only at external I/O seams. `tests/wra_setup_fixture.py` invokes production Python `_setup_action`; Gemini urlopen is deterministic (existing SyntheticProviderObserver), DB factory uses actual libSQL on a test-owned SQLite file. Actual SELECT/schema bootstrap and real provider key/HTTP-error handling run unchanged. This is not installed-webview click-through or remote Turso/Gemini availability evidence.

- First run: empty directory/incomplete eligibility → synthetic names/DB → DB Test Connection → LLM Test Connection → classify EMPTY → initialize BOOTSTRAPPED → Continue/save helper → registry reload/credential validation → backend eligibility → recreated registry/restart Persona load: **PASS**.
- Replacement: configured synthetic Persona → replacement DB preflight with in-memory new token (old store unchanged) → save same canonical slot → reload/restart eligibility → new token resolves, ID/reference/identity and DB bytes unchanged → preserved-token DB lookup: **PASS**.
- Targeted Rust: **11 PASS / 0 FAIL / 0 ignored**. New Python metadata-boundary tests: **2 PASS**. No personal config/DB or credential was used.

### Full local regression

| Gate | Result |
|---|---|
| Python full pytest | **659 PASS / 1 SKIP / 380 subtests PASS**, 61.05s |
| Python compileall app desktop | PASS |
| Frontend full | **162 PASS / 27 files** |
| Frontend lint | PASS, 6 existing warnings |
| Frontend production build | PASS, existing dynamic-import warning |
| Updater config tests | **3 PASS** |
| Release notes tests | **1 PASS** |
| Rust full | **75 PASS / 0 FAIL / 0 ignored** |
| cargo check --locked | PASS |
| git diff --check | PASS |

Counts differ from baseline only by 2 new Python tests and 11 new Rust tests. Existing tests/assertions/skips are unchanged. Mac real sqld lifecycle ran through explicit MINDCORE_TEST_SQLD; Windows retains the existing one-case platform boundary (native sqld 0.24.32 has no supported Windows binary/target). No Windows sqld provision/emulation/fake daemon. All commands bounded, no timeout/hang.

### Scope preservation

WRA_03 = UNCHANGED
WRA_04 = UNCHANGED
WRA_05 = UNCHANGED
WRA_06 = UNCHANGED
WRA_07 = UNCHANGED
WRA_08 = UNCHANGED

`.toolchain/wra-blocker-fix/scope-preservation.json` compares all top-level native functions with audited HEAD and proves only setup/credential-related functions changed. Workflow, updater config, manifests/lockfiles/version, existing recovery harness/platform boundary, schema baseline and frontend UI are byte-identical. Android and feedback repositories untouched; historical accepted Android/feedback results maintained, not rerun. Production secret-pattern scan: 168 files, zero pattern hits (bounded scan). Rustfmt was installed as a local toolchain component only.

### Current decision

WRA_01 = LOCAL_ACCEPTANCE_PASS / WINDOWS_CI_PENDING
WRA_02 = LOCAL_ACCEPTANCE_PASS / WINDOWS_CI_PENDING
WINDOWS_RELEASE_BLOCKERS = WINDOWS_SIGNED_BUILD_ACCEPTANCE_PENDING
DESKTOP_RELEASE_READINESS = PENDING
ANDROID_RELEASE_READINESS = PASS (previous accepted evidence; unchanged)
FEEDBACK_PRODUCTION_PATH = PASS (previous accepted evidence; unchanged)
CROSS_PLATFORM_RELEASE_READINESS = PENDING
RELEASE_READY = FALSE
INSTALLED_WINDOWS_E2E = NOT_RUN (no Windows machine/VM available)
REMOTE_TAG = NO
REMOTE_RELEASE = NO
DESKTOP_VERSION = 0.4.0
ANDROID_VERSION_NAME = 0.1.0
ANDROID_VERSION_CODE = 3

Evidence root: `.toolchain/wra-blocker-fix/` — targeted.log, regression-results.json and individual logs, scope-preservation.json, source-privacy-scan.json, CI metadata/log, signature-verification.log and signed-windows-artifact.json when complete. IMPLEMENTED != ACCEPTANCE-PROVEN; only the stated gates are accepted.
