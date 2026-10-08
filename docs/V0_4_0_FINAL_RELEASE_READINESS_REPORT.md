# MindCore Desktop 0.4.0 Final Release Readiness — BLOCKED

## 이전 blocker 정정 / 최종 판정

local production private key absence was not a product blocker. Production signing is intentionally owned by existing GitHub Actions.

PRODUCTION_SIGNING_AUTHORITY=GITHUB_ACTIONS / LOCAL_PRODUCTION_PRIVATE_KEY_REQUIRED=NO. Production private key를 로컬 복원·export·재생성·대체하지 않았다. Existing updater runtime/endpoint/auto-check/recommendation modal/installer/signature verification architecture 변경 없음.

DESKTOP_RELEASE_READINESS=BLOCKED / ANDROID_RELEASE_READINESS=PASS / FEEDBACK_PRODUCTION_PATH=PASS / CROSS_PLATFORM_RELEASE_READINESS=BLOCKED / RELEASE_READY=FALSE.

현재 유일한 필수 blocker: Windows Release Python gate의 recovery harness가 real native `sqld` 실행 파일을 요구하지만 Windows provisioning이 없다(TEST_INFRA). 사용자가 재사용할 기존 Windows fixture가 없음을 확인했다. 기존 default는 이 Mac의 sibling Android .toolchain 경로이며 Windows runner에는 존재하지 않는다. 이 조건을 skip/mock/assertion 완화로 우회하지 않았다. 따라서 signed Windows installer/signature/artifact gate가 실행되지 않았다. 로컬 private key 부재로 인한 blocker가 아니다.

## Initial audit / logical commits

- Local path: `/Users/noseunghudong-alibujang/Developer/mindcore-desktop`.
- Initial HEAD: `abc1bb91073033848d35993459601da1991d7d4c`.
- Initial branch: `backup/mindcore-desktop-current-2026-09-30`.
- Initial diff --check PASS; 기존 dirty work 보존. reset/clean/stash/restore/rebase/amend 미사용.
- Current branch: `codex/v0.4.0-readiness`.
- Product source commit: `3da63a0d2adc3032ec15acd28951f4e7817bcd69`.
- `801288eb40403e5e443189eeb9e4be591c66f303`: configured message labels, public feedback endpoint, isolated feedback fixtures, ignored local .toolchain.
- `3da63a0d2adc3032ec15acd28951f4e7817bcd69`: Windows corrupt SQLite initialization handle cleanup 및 real-close regression evidence.
- 최종 보고서는 후속 docs-only local commit으로 정리한다. 제품 artifact source SHA와 보고서 commit SHA를 구분한다.

Initial dirty work:

```text
 M .gitignore
 M frontend/src/App.multiPersona.test.tsx
 M frontend/src/App.tsx
 M frontend/src/components/FeedbackDialog.test.tsx
 M frontend/src/components/MessageBubble.test.tsx
 M frontend/src/components/MessageBubble.tsx
 M frontend/src/components/ProductSupport.feedback.test.tsx
 M frontend/src/components/WorkspacePanel.tsx
 M frontend/src/product-support.json
 M frontend/src/services/productSupport.test.ts
?? docs/V0_4_0_FINAL_RELEASE_READINESS_REPORT.md
?? frontend/src/components/WorkspacePanel.messageLabels.test.tsx
?? frontend/src/messageLabels.test.ts
?? frontend/src/messageLabels.ts
```

Feedback repo는 `dd60dae3c8fcd7bdc16b062da3235813340b7d91` / main / clean 유지. Feedback 코드 변경·commit·push 없음. Android signed source는 `05fbcccac2cd6309b393369be8a41d1e85ff95e1`, 후속 보고서 commit은 별도다.

## Desktop local full regression

| Gate | Result |
|---|---|
| Final Python full pytest | 657 PASS / 1 SKIP / 380 subtests PASS / 0 FAIL |
| Python compileall app desktop | PASS |
| Frontend full | 27 files / 162 PASS / 0 FAIL |
| Feedback configured/unconfigured / raw labels / proactive relevant | PASS, full regression에 포함 |
| Lint | PASS / existing 6 no-useless-escape warnings |
| TypeScript/Vite production build | PASS |
| Updater config tests | 3 PASS |
| Release notes/link test | 1 PASS |
| Rust updater enabled tests / cargo check | 64 PASS / PASS |
| Rust updater disabled tests / cargo check | 64 PASS / PASS |
| Final macOS package + sidecar rebuild | PASS (updater-disabled) |
| Final package UI/backend/BOUND_MATCH smoke | PASS |
| Final local package mail-secret/recipient/PAT scan | 5 bundle files / 0 findings |

Final Python handle fix 후 전체 재실행: 140.56s. Frontend/Rust는 Python-only handle change 이전 PASS 실행이며 이후 해당 source 변경 없음. 이전 654 PASS / 3 SKIP 대비: 기존 v0.2.0 released fixture를 실제 Git history에서 복구해 2 SKIP가 PASS가 됐고 handle-close test 1개를 추가해 657 PASS / 1 SKIP다. Assertion 완화나 새 skip 없음.

남은 local SKIP: `tests/database/test_remote_turso_bootstrap.py:29` — `REMOTE_TURSO_TEST_SKIPPED = NO_TEST_CREDENTIALS` (ENVIRONMENT: disposable remote test 전용 credential 미설정). Starlette BlockingPortal deprecation warning 1건.

## Existing production Windows CI

Private Backup repository에는 workflows/secrets/variables가 없으며 기존 production authority는 `Luska-catjun/MINDCORE`의 Windows Release `.github/workflows/release.yml`다. 기존 workflow를 그대로 사용했다. Secret 이름 TAURI_SIGNING_PRIVATE_KEY / TAURI_SIGNING_PRIVATE_KEY_PASSWORD 및 Variable 이름 MINDCORE_UPDATER_PUBKEY의 존재만 확인했으며 private value를 읽거나 출력하지 않았다.

Verification branch push 전 현재 tracked source 437 files 및 outgoing history 27 commits / 195 blobs secret audit: 0 findings. Push는 `codex/v0.4.0-readiness` branch만 대상으로 했다. main force push, tag/release 생성 없음. Existing workflow_dispatch inputs: `build_only=true`, `signed_build_only=true`. Workflow의 Publish GitHub Release는 push event에서만 실행되므로 이번 dispatch에서는 publish하지 않는다. 새 workflow 생성/수정 없음.

- Run1: [37719434130](https://github.com/Luska-catjun/MINDCORE/actions/runs/37719434130), source 801288e — FAIL, 647 tests / 1 failure / 3 errors / 8 skips.
- Real defects: failed corrupt SQLite PRAGMA에서 open handle leak 및 테스트의 sqlite context manager가 close하지 않는 읽기 handle. Windows에서 rename/TemporaryDirectory cleanup에 WinError32가 발생했다. Exception cleanup은 실제 connection.close()를 수행하며 새 real corrupt-file regression test와 read-only closing context를 적용했다. Legacy sync 알고리즘/schema를 확장하지 않았다.
- Run2: [37721096941](https://github.com/Luska-catjun/MINDCORE/actions/runs/37721096941), source 3da63a0 — FAIL, **648 tests / failures=1 / errors=0 / skipped=8**, 214.709s. WinError32 failures는 재발하지 않았다.
- 남은 failure: `test_sqld_controller_stops_and_restarts_same_storage` — `bundled synthetic sqld executable is required` (TEST_INFRA).
- Artifact count: 0. Signed installer, .sig, size/SHA-256/signature verification는 NOT_RUN / UNAVAILABLE. 서명 Secret 결함으로 분류하지 않는다.

### Run2 실제 step 상태

| Step | Result |
|---|---|
| Set up job | SUCCESS |
| Run actions/checkout@v4 | SUCCESS |
| Run actions/setup-python@v5 | SUCCESS |
| Run actions/setup-node@v4 | SUCCESS |
| Run dtolnay/rust-toolchain@stable | SUCCESS |
| Upgrade pip | SUCCESS |
| Install Python dependencies | SUCCESS |
| Install frontend dependencies | SUCCESS |
| Resolve and validate source version | SUCCESS |
| Reject manual release publishing | SKIPPED |
| Run Python tests | FAILURE |
| Compile Python sources | SKIPPED |
| Run frontend tests | SKIPPED |
| Test updater build configurations | SKIPPED |
| Lint frontend | SKIPPED |
| Build frontend | SKIPPED |
| Build Windows sidecar | SKIPPED |
| Verify Windows sidecar dependency boundary | SKIPPED |
| Run Rust tests | SKIPPED |
| Check Rust for Windows | SKIPPED |
| Build unsigned dry-run artifact | SKIPPED |
| Validate release signing configuration | SKIPPED |
| Build signed Windows NSIS artifact | SKIPPED |
| Validate signed build-only artifact | SKIPPED |
| Create release metadata | SKIPPED |
| Run actions/upload-artifact@v4 | SKIPPED |
| Publish GitHub Release | SKIPPED |

### Windows CI의 기존 SKIP 8건

- `test_disposable_remote_database_bootstraps_atomically_and_is_idempotent (tests.database.test_remote_turso_bootstrap.RemoteFreshTursoBootstrapTests.test_disposable_remote_database_bootstraps_atomically_and_is_idempotent) ... skipped 'REMOTE_TURSO_TEST_SKIPPED = NO_TEST_CREDENTIALS'`
- `test_current_backend_startup_upgrades_released_v020_before_runtime_hydration (tests.database.test_turso_schema_contract.TursoSchemaContractTests.test_current_backend_startup_upgrades_released_v020_before_runtime_hydration) ... skipped 'released v0.2.0 fixture is unavailable in this shallow checkout'`
- `test_desktop_initialize_directly_upgrades_the_released_v020_versioned_baseline (tests.database.test_turso_schema_contract.TursoSchemaContractTests.test_desktop_initialize_directly_upgrades_the_released_v020_versioned_baseline) ... skipped 'released v0.2.0 fixture is unavailable in this shallow checkout'`
- `test_desktop_auth_provisioning_keeps_owner_only_permissions_with_umask_022 (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_keeps_owner_only_permissions_with_umask_022) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_leaves_original_config_intact_when_replace_fails (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_leaves_original_config_intact_when_replace_fails) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_preserves_existing_secret_content_and_mode (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_preserves_existing_secret_content_and_mode) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_restricts_a_group_readable_existing_config (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_restricts_a_group_readable_existing_config) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_restricts_an_existing_secret_config (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_restricts_an_existing_secret_config) ... skipped 'POSIX mode bits do not represent Windows ACLs'`

## Local macOS package / actual UI smoke

- Source: `3da63a0d2adc3032ec15acd28951f4e7817bcd69`.
- Path: `.toolchain/readiness-closure-r2/MindCore.app`.
- Zip: `.toolchain/readiness-closure-r2/MindCore-macOS-0.4.0-local.zip`.
- Bytes: 34,440,582.
- SHA-256: `33edda50ba0a0a74c6fc730460b62efb0b5f3a8157148122658ad63eee3fd1c3`.
- Canonical `MINDCORE_UPDATER_DISABLED=1 npm run tauri:build -- --bundles app` PASS.
- Local macOS bundle에 `codesign --force --deep --sign -`를 적용하고 `codesign --verify --deep --strict` PASS. Local ad-hoc, Developer ID/notarization 없음. Production Windows signing을 대체하지 않는다.

실제 사용자 app를 정상 종료한 뒤 MINDCORE_ENV_FILE을 test-owned config로 지정하여 final package를 실행했다. Unique synthetic Persona, real schema24 sqld, identity/registry/user labels 및 두 historical message rows만 사용했다. 첫 HTTP fixture는 create_pool의 shared_database_https_required에 의해 거부되어 TEST_INFRA로 기록했다. 이후 test-owned HTTPS loopback proxy와 임시 local CA를 **test process의 SSL_CERT_FILE**에만 적용했다. OS/global trust, production config, provider key/Persona DB를 변경하지 않았다. HTTPS native libSQL select1 검증 후 package 부팅 PASS. 실제 native ready_success 및 Python lifespan_complete 로그를 확인했다.

CUA로 실제 package UI를 조작하고 다음을 확인했다:

- Chat surface load / backend connected / Persona BOUND_MATCH(`Persona 연결됨`) PASS.
- Chat historical messages: Synthetic Release Persona / Synthetic Release User label PASS.
- Data Management > Messages: 동일 configured labels PASS; raw role 노출 없음.
- App Settings / Persona 선택과 관리 / Persona Connection / DB 연결됨 / provider configured UI PASS.
- Proactive: off 상태, 최소 간격 1800, quiet-time, 설정 저장 및 시스템 알림 control 표시 PASS. 설정 변경/새 scheduler/engine 구현 없음.
- Update UI: version0.4.0, 업데이트 확인 disabled(빌드 모드와 일치) PASS.
- What's New: bundled0.4.0 notes 및 공지 링크 표시 PASS.
- Feedback: 제목/내용/category/preview/diagnostics unchecked UI PASS; 실제 메일 재전송 없음.

사용자 실제 configuration 5files의 before/after SHA-256 동일. Test-owned package/sidecar/sqld/proxy/AVD는 종료했다. 개인 Android device 및 실제 Persona DB destructive test 없음. 기존 사용자 앱/AVD를 다시 열어 복구한다.

## Feedback / UI product contract

Accepted direct endpoint `https://mindcore-feedback.nibung.workers.dev/feedback` 보존. Workers Free → Gmail API HTTPS / SMTP NOT_USED / monthly required cost0. 기존 Worker regression56 PASS 및 security audit21files0findings 유지. 실제 activation 및 Desktop/Android Gmail receipt는 이전 사용자 수신 확인으로 ACCEPTANCE-PROVEN이며 이번에 불필요한 메일을 추가 발송하지 않았다. Current Desktop frontend 및 Android JVM feedback targeted regression PASS.

DIRECT_ENDPOINT_CONFIGURED=TRUE / ACTUAL_GMAIL_E2E=PASS / CLIENT_MAIL_SECRET=NO / CLIENT_RECIPIENT_EMAIL=NO. OAuth values는 Worker Secrets만 담당하며 client contract/error semantics/diagnostics opt-in/GitHub fallback 유지.

RAW_ROLE_VISIBLE_IN_PRODUCT_UI=NO / PERSONA_MESSAGE_LABEL=CONFIGURED_PERSONA_DISPLAY_NAME / USER_MESSAGE_LABEL=CONFIGURED_USER_DISPLAY_NAME / DB_ROLE_COMPATIBILITY_PRESERVED=YES. Persona switch/current configured user after restart regression PASS. Database/API diana/user roles 및 provider/context 의미 유지. Desktop/Android common message renderer presentation만 resolve하며 example names production hardcode 없음.

DESKTOP_PROACTIVE_UI=WIRED / ANDROID_PROACTIVE_RUNTIME=NOT_IMPLEMENTED. Dead Android toggle 없음. Shared Persona는 one shared authoritative DB; 역사적 peer/replica/delta sync는 새 foundation으로 사용하지 않았다. IMPLEMENTED != ACCEPTANCE-PROVEN.

## Android closure / distribution / final blocker

Android Python121 PASS / JVM37 PASS / full instrumentation107 discovered,68 PASS,39 existing opt-in SKIP,0 FAIL,0 NOT_RUN. 모든39skip 이유는 Android 최종 보고서에 기록. Debug/unsigned release/lint/signature/permanent cert match/install/same-key upgrade PASS. APK SHA-256 `7a8d95dc06fe4085a9caba37c9541c53e4330ed82651ee0b320a529f62849957`; certificate `53e1664cf9740078a31b86f46f7511c0827623ae3cab6cfe091d9672c5f50466`.

VERIFICATION_BRANCH_PUSH=YES (Desktop source only) / REMOTE_TAG=NO / REMOTE_RELEASE=NO / PUBLIC_UPDATER_DELIVERY=NOT_RUN / Android public APK upload=NO. Versions Desktop0.4.0 / Android0.1.0 code3 unchanged. Existing v0.2.0 tag는 fetch했으며 새 tag 생성 없음.

RELEASE_READY=FALSE. 남은 필수 gate는 existing Windows CI의 real sqld fixture provisioning과 그 뒤의 signed Windows artifact validation이다. Test skip/assertion 완화로 gate를 억지로 통과시키지 않았다. 이번 결과를 public release approval이나 실제 updater delivery proof로 사용하지 않는다.

Evidence: `.toolchain/readiness-closure-r2/` — start.json, regression-results.json, python-final.log, ci-final.json, ci-final-failed.log, local-package-smoke.json, final-package-privacy.json, user-config-after.json. 이전 report는 previous-report.md에 보존.
