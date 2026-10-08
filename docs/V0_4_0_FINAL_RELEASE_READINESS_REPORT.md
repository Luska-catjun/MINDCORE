# MindCore Desktop 0.4.0 Final Release Readiness — PASS

기준일: 2026-10-08 (Asia/Seoul).

## 최종 판정

DESKTOP_RELEASE_READINESS=PASS
ANDROID_RELEASE_READINESS=PASS (기존 accepted result 유지; 이번 작업에서 변경/재실행 없음)
FEEDBACK_PRODUCTION_PATH=PASS (기존 accepted Gmail E2E 유지)
CROSS_PLATFORM_RELEASE_READINESS=PASS
RELEASE_READY=TRUE

TRUE는 현재 source에서 기존 production GitHub Actions signing path가 실제 Windows signed NSIS 및 updater signature artifact를 생성했고 필수 gate가 통과했다는 뜻이다. Public tag/release/updater delivery는 아직 실행하지 않았다. 실제 Windows UI installation/update delivery를 새로 실행했다고 주장하지 않는다.

## 이전 blocker 판정 정정

local production private key absence was not a product blocker. Production signing is intentionally owned by existing GitHub Actions.

Windows release가 sqld.exe를 요구한다는 이전 해석도 정정한다. 실제 문제는 나중에 추가된 **Unix/macOS real-sqld integration case가 Windows unittest discovery에 포함된 test scope 오류**였다. Native sqld 0.24.32는 Windows native binary/target을 지원하지 않는다. Production Windows updater/signing architecture의 결함이나 prerequisite가 아니다.

사용자의 최신 correction에 따라 lifecycle **한 case만** `unittest.skipIf(sys.platform == "win32", explicit reason)` boundary로 분리했다. Native exe creation/port/emulation/provisioning/fake daemon/mock replacement 없음. 기존 recovery harness test와 모든 assertions/body를 보존했다. macOS/Linux에서는 decorator가 false이므로 real lifecycle case가 계속 실행되며 fixture가 없으면 기존 assertion으로 실패한다. Linux에서 실행했다고 별도로 주장하지 않는다.

- Platform-neutral 3 cases: provider observer, cognition observer, cross-device barrier — Windows에서 모두 PASS.
- Native lifecycle case — Windows에서 명시적 NOT APPLICABLE / platform SKIP; Mac에서 실제 sqld로 PASS.
- Native sqld: upstream tursodatabase/libsql, version0.24.32, commit40c272de85ee4e62d722c5ccae5da2e76b4253a1.
- [Pinned upstream build instructions](https://github.com/tursodatabase/libsql/blob/40c272de85ee4e62d722c5ccae5da2e76b4253a1/docs/BUILD-RUN.md#build-from-source-using-rust), [official release](https://github.com/tursodatabase/libsql/releases/tag/libsql-server-v0.24.32).

## 변경 범위 / Git

Initial HEAD: c4b7cae0a5ac1d25f7e781a11718974cb39c7406, branch codex/v0.4.0-readiness, clean, diff --check PASS. Existing commits를 보존했고 reset/clean/stash/restore/rebase/amend 미사용.

1. `c27d9d0d2567db40c16be8c597be6f6fe75d0e48`: sys import 및 lifecycle Windows platform decorator 5lines.
2. `20275953e96bc438eb9bec38f080903c73dee100`: .gitattributes 2lines, release-notes/*.md text eol=lf.
3. 후속 docs-only commit: 이 최종 보고서; artifact source SHA와 구분.

Release workflow `.github/workflows/release.yml`는 요청 시작 전과 byte-identical. CI workflow, app/desktop/frontend product source 및 updater runtime/endpoint/auto-check/recommendation modal/installer/signature verification/signing helper/key/secret/version은 변경하지 않았다. Product source는 이전 accepted Mac package source3da63a0과 동일하다. Android HEAD006309efa2d5e1ff4cd188ed96ab0884731cc2bb 및 source/보고서/artifact를 변경하지 않았다.

Verification branch만 push. Main 변경/force push 없음. 로컬 tool/evidence/.exe/.sig/public verification key는 ignored .toolchain에 있으며 binary source commit 없음.

## Local targeted validation

- Real sqld fixture를 explicit MINDCORE_TEST_SQLD로 지정한 Mac harness: **4 PASS / 0 FAIL / 0 SKIP**, 11.057s.
- AST 비교: 네 test method body 및 assertions 동일; platform-neutral 3cases undecorated.
- Python syntax compile PASS; diff --check PASS.
- Windows checkout CRLF 문제: 실제 git clone + core.autocrlf=true checkout에서 기존 pretest heading 검사 실패를 재현했다. Canonical notes LF header가 CRLF로 바뀌었다.
- .gitattributes를 적용한 동일 checkout에서 notes bytes가 Git blob과 정확히 같음 PASS. 기존 loader/body/heading/version/size checks 및 notes content는 변경하지 않았다.
- Existing release notes test: 1 PASS / 0 SKIP.

## Windows CI 시도 및 최종 current-source acceptance

기존 Windows Release workflow, repository Luska-catjun/MINDCORE, branch codex/v0.4.0-readiness. 기존 dispatch inputs **build_only=true, signed_build_only=true**. Production signing authority는 기존 GitHub Actions Secrets TAURI_SIGNING_PRIVATE_KEY / TAURI_SIGNING_PRIVATE_KEY_PASSWORD 및 Repository Variable MINDCORE_UPDATER_PUBKEY다. Private value를 조회/export/출력하지 않았다.

- [37723314402](https://github.com/Luska-catjun/MINDCORE/actions/runs/37723314402), sourcec27d9d0: Python648 tests / skipped9 / 0 failures PASS. Frontend pretest는 CRLF notes heading 검사에서 실패했다. Frontend suite 자체는 시작되지 않았다. TEST_INFRA checkout boundary를 .gitattributes로 고쳤다.
- [37723781839 attempt1](https://github.com/Luska-catjun/MINDCORE/actions/runs/37723781839/attempts/1), source2027595: Python이 traceback/unittest failure summary 없이 exit1로 종료했다. 마지막 로그는 schema drift case였다. 정확한 종료 원인은 **UNKNOWN**이며 flake/product/환경 중 하나로 근거 없이 단정하지 않는다. 로그를 보존했다. 이 실패를 숨기거나 테스트를 완화하지 않았다.
- **[37723781839 attempt2](https://github.com/Luska-catjun/MINDCORE/actions/runs/37723781839/attempts/2)**: 동일 workflow/source를 변경 없이 재실행했고 **SUCCESS**. 이 성공만으로 이전 갑작스러운 종료의 원인을 확정했다고 주장하지 않는다. 현재 final complete run에 failures 없음.
- Actual artifact source: **20275953e96bc438eb9bec38f080903c73dee100**.

### 최종 regression

| Gate | Result |
|---|---|
| Windows Python portable full unittest | 648 discovered / 639 PASS / 9 SKIP / 0 FAIL / 0 ERROR, 201.324s |
| Windows portable recovery harness | 3 PASS; native sqld case만 platform SKIP |
| Python compile | PASS |
| Frontend | 27 files / 162 PASS / 0 FAIL |
| Updater config tests | 3 PASS |
| Lint / frontend production build | PASS (existing warnings 유지) |
| Windows sidecar build / dependency verification | PASS / PASS |
| Rust Windows tests | 64 PASS / 0 FAIL / 0 ignored |
| Windows cargo check | PASS |
| Production signing configuration | PASS |
| Signed Windows NSIS / .sig existence/nonempty | PASS |
| Artifact upload | PASS |
| Production public key cryptographic verification | PASS (local minisign0.12) |

### 최종 workflow 전체 step 상태

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
| Run Python tests | SUCCESS |
| Compile Python sources | SUCCESS |
| Run frontend tests | SUCCESS |
| Test updater build configurations | SUCCESS |
| Lint frontend | SUCCESS |
| Build frontend | SUCCESS |
| Build Windows sidecar | SUCCESS |
| Verify Windows sidecar dependency boundary | SUCCESS |
| Run Rust tests | SUCCESS |
| Check Rust for Windows | SUCCESS |
| Build unsigned dry-run artifact | SKIPPED |
| Validate release signing configuration | SUCCESS |
| Build signed Windows NSIS artifact | SUCCESS |
| Validate signed build-only artifact | SUCCESS |
| Create release metadata | SUCCESS |
| Run actions/upload-artifact@v4 | SUCCESS |
| Publish GitHub Release | SKIPPED |

### Windows SKIP 9건 — 전부 명시

기존8skip(credential1, shallow v0.2.0 fixture2, Windows에 적용되지 않는 POSIX permissions5)에 unsupported native sqld integration1의 explicit platform boundary가 추가됐다. 다른 test skip/assertion weakening 없음. SKIP는 PASS로 합산하지 않는다.

- `test_disposable_remote_database_bootstraps_atomically_and_is_idempotent (tests.database.test_remote_turso_bootstrap.RemoteFreshTursoBootstrapTests.test_disposable_remote_database_bootstraps_atomically_and_is_idempotent) ... skipped 'REMOTE_TURSO_TEST_SKIPPED = NO_TEST_CREDENTIALS'`
- `test_current_backend_startup_upgrades_released_v020_before_runtime_hydration (tests.database.test_turso_schema_contract.TursoSchemaContractTests.test_current_backend_startup_upgrades_released_v020_before_runtime_hydration) ... skipped 'released v0.2.0 fixture is unavailable in this shallow checkout'`
- `test_desktop_initialize_directly_upgrades_the_released_v020_versioned_baseline (tests.database.test_turso_schema_contract.TursoSchemaContractTests.test_desktop_initialize_directly_upgrades_the_released_v020_versioned_baseline) ... skipped 'released v0.2.0 fixture is unavailable in this shallow checkout'`
- `test_desktop_auth_provisioning_keeps_owner_only_permissions_with_umask_022 (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_keeps_owner_only_permissions_with_umask_022) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_leaves_original_config_intact_when_replace_fails (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_leaves_original_config_intact_when_replace_fails) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_preserves_existing_secret_content_and_mode (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_preserves_existing_secret_content_and_mode) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_restricts_a_group_readable_existing_config (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_restricts_a_group_readable_existing_config) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_desktop_auth_provisioning_restricts_an_existing_secret_config (tests.test_desktop_backend.DesktopBackendTests.test_desktop_auth_provisioning_restricts_an_existing_secret_config) ... skipped 'POSIX mode bits do not represent Windows ACLs'`
- `test_sqld_controller_stops_and_restarts_same_storage (tests.test_m7333a2125_recovery_harness.RecoveryHarnessPrimitiveTests.test_sqld_controller_stops_and_restarts_same_storage) ... skipped 'Native sqld 0.24.32 supports macOS/Linux only; no supported Windows binary/target.'`

## Signed Windows artifact 독립 검증

- Run ID: 37723781839 / attempt2.
- Source commit: `20275953e96bc438eb9bec38f080903c73dee100`.
- CI artifact: mindcore-windows-signed-build-only, artifact ID11527622907.
- GitHub artifact archive size: 30,429,933 bytes; GitHub digest sha256:16ff6b9e458452657d14f6653909c790902f12487b5e42eb7f60abcd5db27d9c (installer hash와 구분).
- Local installer: `.toolchain/windows-platform-gate/artifacts/MindCore_0.4.0_x64-setup.exe`.
- Installer size: 30,439,335 bytes.
- **Installer SHA-256: `065f7830f448e6154a64d049a4dbfb5cf8bff619d6c5b052ec48731b7e712874`**.
- Updater signature: `MindCore_0.4.0_x64-setup.exe.sig`, 420bytes, exists/non-empty PASS.
- Signature file SHA-256: `e5895e6d3b735a4e541c42b1382f5f07fae8de74c6623bd09349196cc1020209`.
- Tauri base64 signature envelope를 decode하고 기존 repository public key로 minisign0.12 verify를 실행했다. Result: **Signature and comment signature verified**.
- Public key source: existing GitHub Repository Variable MINDCORE_UPDATER_PUBKEY. 새 key generation/replacement, private key local export 없음.
- Windows .exe를 Mac에서 실행하지 않았다. 여기서 signature PASS는 Tauri updater minisign 검증이며 별도 Authenticode 인증을 주장하지 않는다.

## 기존 accepted cross-platform 상태 보존

Desktop local Mac Python657 PASS / 1 SKIP / 380 subtests PASS, frontend162 PASS, Rust updater enabled/disabled 각각64 PASS 및 checks PASS, updater-disabled packaged UI/backend/BOUND_MATCH smoke PASS는 이전 accepted evidence를 유지한다. 이번 product source 변경 없음. Mac test-owned lifecycle 추가 실행은 위4PASS다. Local Mac package source는3da63a0, zip SHA33edda50ba0a0a74c6fc730460b62efb0b5f3a8157148122658ad63eee3fd1c3이다.

Android accepted: Python121 PASS, JVM37 PASS, full instrumentation107 discovered /68 PASS /39 reasoned SKIP /0 FAIL /0 NOT_RUN, signed APK/permanent cert/install/same-key upgrade PASS. APK SHA7a8d95dc06fe4085a9caba37c9541c53e4330ed82651ee0b320a529f62849957. 이번 Android 변경 없음.

Feedback direct endpoint 설정 및 Workers Free → Gmail API HTTPS path와 이전 사용자 Gmail activation/Desktop/Android receipt acceptance PASS 유지. 불필요한 메일 재전송/endpoint redesign 없음. CLIENT_MAIL_SECRET=NO / CLIENT_RECIPIENT_EMAIL=NO.

RAW_ROLE_VISIBLE_IN_PRODUCT_UI=NO / PERSONA_MESSAGE_LABEL=CONFIGURED_PERSONA_DISPLAY_NAME / USER_MESSAGE_LABEL=CONFIGURED_USER_DISPLAY_NAME / DB_ROLE_COMPATIBILITY_PRESERVED=YES. DESKTOP_PROACTIVE_UI=WIRED / ANDROID_PROACTIVE_RUNTIME=NOT_IMPLEMENTED. 기존 architecture/accepted state 보존.

## Distribution / 최종 결론

VERIFICATION_BRANCH_PUSH=YES
REMOTE_TAG=NO
REMOTE_RELEASE=NO
PUBLIC_UPDATER_DELIVERY=NOT_RUN
DESKTOP_VERSION=0.4.0
ANDROID_VERSION_NAME=0.1.0
ANDROID_VERSION_CODE=3
RELEASE_READY=TRUE

남은 것은 실제 public distribution이며 이번 작업 범위에서 수행하지 않았다. IMPLEMENTED != ACCEPTANCE-PROVEN: current Windows build/signature gates는 실제 실행으로 검증됐고 public updater delivery는 아직 NOT_RUN이다.

Evidence: `.toolchain/windows-platform-gate/` — start.json, platform-boundary.json, scope-static-audit.json, product-preservation.json, mac-harness.log, crlf-before.json, crlf-after.json, ci-attempt1.log, ci-attempt2-failed.log, python-abrupt-exit.json, ci-final.json/log, run-final-metadata.json, artifact-inventory.json, signed-windows-artifact.json, signature-verification.log, artifacts/. 이전 보고서는 previous-report.md에 보존했다. Final docs-only commit 이후 clean/diff-check state를 별도 final-git-state.json에 기록한다.
