# MindCore Desktop 0.4.0 Navigation / Product UI Final Polish 보고서

## 시작 상태

- 경로: `/Users/noseunghudong-alibujang/Developer/mindcore-desktop`
- branch: `backup/mindcore-desktop-current-2026-09-30`
- 시작 HEAD: `e012364993ca18ffc5e3eba85cab03a745d44234`
- tracked working tree clean, 기존 `?? .toolchain/` 보존.
- 시작과 최종 `git diff --check` 통과. 금지된 reset/clean/stash/restore/rebase/amend 없음.

## 구현

1. 7개 데이터 분류를 underline navigation으로 변경했다. 각 분류의 15개 domain ID/API/correction 기능은 유지하면서 하위 항목은 작은 글자/얇은 indicator로 구분했다. 그룹별 마지막 선택을 유지한다.
2. Sidebar 폭을 196px로 조정하고 status를 compact하게 합쳤다. 전체 폭 Live backend 행과 Observation Surface label을 제거했다.
3. 설정을 Persona / 연결 / AI 설정 / 먼저 말 걸기 / 업데이트 / 일반으로 구성했다. 선택한 설정 범주는 workspace state에 두어 기존 native sidecar 재시작 뒤에도 유지한다.
4. 지속 mount되는 updater의 controls만 settings DOM ref에 portal로 붙였다. auto-check/session dismissal/modal은 기존 shell에 유지한다. setup에도 release notes와 Feedback이 남는다. ready workspace에는 footer 업데이트 오류/재시도/Feedback 중복이 없다.
5. 기존 PersonaManager 내부 proactive 설정을 새 canonical 설정 범주로 옮겼다. 기존 native get/update 명령, validation, Persona별 config 저장, active sidecar 재시작/실패 rollback을 그대로 사용한다.
6. quiet start/end, quiet enabled, 300~604800초 범위의 현재 cooldown, proactive enabled를 노출했다. 임의의 dropdown 값으로 기존 설정을 덮어쓰지 않는다. 저장 전에는 실제 저장된 policy로 상태를 표시하고 실패 시 draft를 보존한다. stale Persona 응답을 차단한다.
7. 실제 OS 권한 상태를 반환하지 않는 Desktop notification plugin stub을 허용 상태로 표시하지 않는다. 고정된 OS 알림 설정 주소만 여는 native 명령과 안내를 제공한다. macOS 알림 페이지 실제 진입을 확인했고 OS 권한 자체를 변경하지 않았다.
8. updater config generator가 canonical notification/dialog capability를 덮어쓰던 빌드 문제를 수정했다. 양쪽 updater 모드에서 기존 capability를 보존하고 disabled 모드에서 updater grant만 제거한다. 임의 URL/명령 입력이나 새 secret API는 추가하지 않았다.

## 먼저 말 걸기 범위

```
DESKTOP_PROACTIVE_RUNTIME=SUPPORTED
PROACTIVE_UI=PASS
PROACTIVE_ENABLED_CONTROL=PASS
QUIET_HOURS_CONTROL=PASS
COOLDOWN_CONTROL=PASS
NOTIFICATION_CONTROL=PASS (OS 설정 진입; 앱 내부 가짜 권한 toggle 없음)
PROACTIVE_RUNTIME_BEHAVIOR_CHANGED=NO
ANDROID_PROACTIVE_RUNTIME=NOT_SUPPORTED
PROACTIVE_RUNTIME=DESKTOP_ONLY
FAKE_ANDROID_PROACTIVE_CONTROL=NO
```

`app/` scheduler/config/storage 및 proactiveEvents.ts 변경 없음. startup grace, activity checks, repeated-no-response backoff/suppression, durable gate/event path 그대로다. 사용자 Persona의 proactive를 테스트 목적으로 켜거나 실제 provider 호출을 유발하지 않았다.

Persist 검증은 기존 native Persona별 설정 serialize/read-back tests와 UI 저장/remount fixture를 포함한다. 실제 사용자 profile에서는 저장하지 않고 기존 값의 로드만 확인했다. OS notification delivery와 실사용자 설정 변경 후 재시작 실험은 별도로 수행하지 않았다.

## 최종 회귀 및 build

| 명령/검증 | 실제 결과 |
|---|---|
| `npm --prefix frontend test -- --run` | 24 files / 129 PASS / 0 FAIL |
| `npm --prefix frontend run test:updater-config` | 3 PASS |
| `.venv/bin/python -m pytest tests/services/test_autonomy_runtime.py tests/integration/test_autonomy_runtime.py tests/integration/test_autonomy_lifecycle.py tests/test_desktop_backend.py -q` | 52 PASS + 3 subtests PASS |
| `cargo test --locked --manifest-path frontend/src-tauri/Cargo.toml` | 64 PASS / 0 FAIL |
| TS / Vite production build | PASS, 217 modules |
| `npx --prefix frontend tauri build --bundles app --config frontend/src-tauri/tauri.updater.conf.json --config '{"bundle":{"createUpdaterArtifacts":false}}'` | PASS, MindCore.app |

Frontend 이전 124에서 129로 증가: 기존 proactive test를 새 위치로 이동하고 관련 3개/portal 2개 검증을 추가했다. Rust 이전 63에서 고정 OS settings 목적지 테스트 1개를 추가해 64다. Python은 이번 관련 suite만 선정했으므로 이전 전체 숫자와 직접 비교하지 않는다.

기존 Vite dynamic import 경고, Starlette deprecation 경고는 있었지만 최종 실패는 없다. 임의 assertion 약화나 test-only production bypass 없음.

## 실제 설치본 smoke

- 최종 bundle을 `/Applications/MindCore.app`에 설치했다. 기존 설치본은 `/Applications/MindCore-before-final-polish-20261007-180951.app` 형태의 timestamp backup으로 보존했다. 정확한 경로는 아래 install.json 참조.
- bundle/installed file 전체 byte hash 일치 확인.
- launch, 현재 Persona `BOUND_MATCH`에 대응하는 연결됨 표시, DB health connected, 기존 대화 읽기 확인.
- 네 primary nav, 데이터 7개 분류/진단 하위 통계·디버그, 설정, Feedback dialog, 업데이트/실패/재시도 위치, bundled release notes/공지 링크, 먼저 말 걸기 기존 값, 시스템 알림 페이지 진입 확인.
- 실제 대화 전송/Feedback 제출, Persona 전환/삭제, provider 재설정, 사용자 proactive 저장은 하지 않았다.
- config/Identity 파일 5개 hash 비교: 변경 0 / 추가 0 / 삭제 0. DB reset/migration 없음.

## 증거

`.toolchain/final-polish/`의 frontend.log, updater-config.log, python.log, rust.log, app-build.log, emoji-audit.json, install.json, config-integrity.json. 이 폴더는 기존 untracked 환경 폴더로 유지하고 commit하지 않았다.

## 로컬 commit

- Desktop implementation: `34bccdf61ff6f1e2189c51d4eaa665a83327115c`
- Android implementation: `fb0889cd74e3035136d7473aec74e04b9b1b5bd3`
- 보고서 commit은 implementation과 분리하며 최종 응답에 실제 SHA를 기록한다. 이 파일의 commit은 `git log -1 --format=%H -- docs/V0_4_0_NAVIGATION_PRODUCT_UI_FINAL_POLISH_REPORT.md`로 조회할 수 있다.

## 판정과 검증 범위

최종 UI polish 판정: **PASS**, 다음 결정: **PRODUCT_UI_READY**.
구현 완료와 acceptance 검증을 구분한다. 아래 PASS는 이번 UI/navigation 및 관련 회귀 범위의 결과다. 전체 M7 또는 cognition suite를 다시 실행했다는 뜻이 아니다.

- Desktop 0.4.0, Android 0.1.0 / versionCode 3 유지.
- 정확히 네 개의 최상위 개념: 대화 / 데이터 관리 / 앱 설정 / 피드백.
- Persona DB, Identity, config reset, schema migration, peer/replica 확장 없음.
- LOCAL_ONLY는 한 기기의 authoritative DB, SHARED는 양 기기가 직접 접근하는 하나의 authoritative Persona DB라는 기존 모델 유지.
- 기존 Feedback transport 및 updater 검증/다운로드/설치 경로 유지. 실제 의견 제출, 공개 배포, remote push/tag/release 없음.
- 제품 navigation과 설정 범주를 한국어로 정리했다. 원시 진단 항목, API 고유명, 기존 release notes 문구는 유지했다.
- 제품 소스에서 Unicode emoji 범위 감사 결과 0건. 사용자 대화와 테스트 fixture의 텍스트는 삭제하지 않았다.

## 공통 acceptance 결과

| 항목 | 결과 |
|---|---|
| DESKTOP_NAVIGATION_POLISH | PASS |
| ANDROID_NAVIGATION_POLISH | PASS |
| CROSS_PLATFORM_INFORMATION_ARCHITECTURE | PASS |
| PRODUCT_UI_FINAL_POLISH | PASS |
| DESKTOP_TOP_LEVEL_NAV_COUNT / ANDROID_TOP_LEVEL_NAV_COUNT | 4 / 4 |
| PILL_BUTTON_OVERUSE | REMOVED — navigation 기준, 실행 action 버튼은 유지 |
| FULL_WIDTH_STATUS_CLUTTER | REMOVED |
| FEEDBACK_DUPLICATION | NO — 준비된 workspace의 global/footer 중복 제거 |
| LANGUAGE_CONSISTENCY | PASS — 제품 navigation/설정 label 기준 |
| NAVY_BLUE_CYAN_TEAL | PASS |
| EMOJI_IN_PRODUCT_UI | 0 |
| LEGACY_GREEN_BRANDING | 0 — 성공/오류 상태 색상은 branding과 구분 |
| OVER_DECORATION / OLD_ENTERPRISE_SQUARE_UI | NO / NO |
| UI_COHERENCE | PASS |
| NEW_PRODUCT_REGRESSIONS | 0 — 아래 최종 실행 범위 |
| REMOTE_PUSH / REMOTE_TAG / REMOTE_RELEASE | NO / NO / NO |

## 아이콘

- Desktop canonical: `frontend/src-tauri/icons/icon.png`.
- SHA-256: `49e23d6acfecd076b861521dd91c7ca65e01a51c85ea97aa4dd6d07a391fc62c`.
- Android canonical bitmap: `android/app/src/main/res/drawable-nodpi/mindcore_brand.png`, Desktop와 byte 동일.
- Manifest: `@mipmap/ic_launcher`, `@mipmap/ic_launcher_round`; v26 adaptive foreground/background, density별 legacy fallback 유지.
- foreground inset 29%, 검은 background와 기존 blue/orbit artwork 유지. 로고 재디자인·색상 변경·왜곡 없음.
- DESKTOP_ANDROID_ICON_BRAND_MATCH=YES, ANDROID_ADAPTIVE_ICON=PASS, ANDROID_LEGACY_ICON=PASS.
- API35에서 adaptive/round drawable과 마스크 전 foreground 안전 영역을 검증하고 실제 launcher app drawer의 MindCore 표시를 확인했다.
- legacy PASS는 resource 정합성 및 legacy XML 직접 inflate 검증 범위다. 별도 API24/25 기기를 실행했다는 의미는 아니다.

## 한계와 다음 단계

Desktop 기본 update endpoint는 현재 `.invalid` 개발 주소라 실제 원격 확인은 실패 상태를 정상 표시한다. 업데이트 권장/나중에/상세 보기/다운로드/설치 handoff는 기존 service fixture 테스트로 검증했다. 새 원격 채널 배포나 실제 앱 업데이트 설치까지 acceptance-proven이라고 주장하지 않는다.

Android Release build는 unsigned APK compile/package 경로의 PASS다. signing credential, update channel, production dependency를 변경하지 않았다. 이번 milestone은 공개 배포 승인이 아니다.

NEXT_DECISION=PRODUCT_UI_READY. Feedback endpoint 및 publication은 별도 결정으로 남긴다.
