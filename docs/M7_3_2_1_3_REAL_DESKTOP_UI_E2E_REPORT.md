# MindCore M7.3.2.1.3 — 실제 Desktop UI E2E 보고서

**결과: FAIL / NO-GO.** 이번 run에서 실제 Tauri 앱을 실행하고 Sync 화면의 pairing export를 native file save dialog로 완료했다. React click, Tauri command, packaged sidecar, schema-24 sync state/identity 저장까지 한 경로를 연결해 확인했다. 후속 peer 앱이 시작 오류를 보였으며, macOS 재잠금으로 자동화가 차단되었다. Android 호스트에는 JRE/SDK/adb가 없어 cross-platform 제품 UI acceptance를 수행할 수 없었다.

## 시작 상태와 범위

- Base: `43322b9030a2994a01fa81abd8978fcb9b2a8d9c`, branch `feature/m73-desktop-sync`; dirty M7.3.2 누적 변경을 유지했다.
- Android base: `5de7a96d995d22e4f8735c6e85923dab06a8e9a5`, branch `main`; 두 저장소 schema version은 24다.
- Scope는 `MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS`로 유지. schema 25, 새 sync protocol, cloud/server, auto/background sync은 추가하지 않았다.
- Tauri npm API/CLI minor version 충돌로 app bundle 생성이 실패하자 `@tauri-apps/api@2.11.1`, `@tauri-apps/plugin-dialog@2.7.3` exact pin을 반영해 CLI/API 호환성을 맞췄다. 두 테스트 앱 설정은 격리된 bundle id를 쓴다.

## 실제 앱 harness와 증거

- 실제 unsigned release `.app`을 updater disabled 설정으로 빌드하고 실행했다: `com.luskacat.mindcore.m73213e2e`. production app/bundle ID 및 production Persona는 열지 않았다.
- UI automation은 CUA macOS accessibility tree와 실제 native file dialogs다. Sync / Devices로 이동하여 Backend connected, synthetic Persona, device identity/fingerprint를 봤다. **Export pairing file** 버튼을 눌러 macOS Save panel에서 실제 artifact를 생성했고 React status의 export 성공 상태를 확인했다.
- Artifact: `tests/m73213_ui_e2e/artifacts/desktop-device.mindcorepair`. JSON parse와 pairing artifact 필드(`artifact_type`, `device_id`, `fingerprint`, `protocol_version`, `public_key`) 확인 PASS; secret material은 값으로 출력하지 않았다.
- Isolated app-support sidecar DB의 read-only 집계: metadata DB에서 persona baseline/sync state/sync meta 각 1 row; security DB에 identity anchor 1 row. trust/replay/conflict/resolution/remote-seen/pending-artifact row는 각 0. 실제 UI export로 baseline/sync metadata/identity가 저장된 것을 확인했다.
- 별도 `com.luskacat.mindcore.m73213peer` app은 창을 열었지만 “MindCore could not start”를 표시했다. Retry를 시도한 뒤 CUA가 Mac locked를 보고해 추가 조작을 중단했다. 사용자에게 비밀번호/코드를 요청하거나 입력하지 않았다.

| 연결 단계 / UX | 판정 | 근거 |
|---|---|---|
| 실제 Tauri app launch | PASS | 격리된 release app process와 창 확인 |
| 실제 React Sync UI | PASS | accessibility tree 및 실제 버튼 조작 |
| React → Tauri IPC | PASS | pairing export command가 성공 상태와 산출물을 반환 |
| Tauri → sidecar / sync engine | PASS (단일 export) | sidecar SQLite identity/baseline/sync state 변화 확인 |
| 실제 pair export + native Save | PASS | `.mindcorepair` 파일 생성 및 JSON 구조 확인 |
| pair import/open dialog/parser | FAIL | 제품 UI에서 실행하지 못함 |
| fingerprint confirm, trust persistence, revoke | FAIL | 제품 UI에서 실행하지 못함 |
| sync export/import, replay result UI | FAIL | 제품 UI에서 실행하지 못함 |
| conflict inbox, mutable/tombstone/integrity resolution UI | FAIL | 제품 UI에서 실행하지 못함 |
| unsupported remote Persona safe rejection/no partial write | UNVERIFIED | product UI/backend invariant를 이번 실제 앱 run에서 검사하지 않음 |
| 전체 Desktop product E2E | FAIL | 하나의 export만 완료; 나머지 required action matrix 미충족 |

## Cross-platform acceptance

Pairing artifact는 Desktop UI에서 실제 생성됐지만 Android SAF로 전달·선택되지 않았다. A→D 방향, 양쪽 trust, paired app restart, D→A/A→D sync와 replay, append convergence, mutable 및 tombstone conflict resolution, resolution-vs-resolution 교환, Multi-Persona 격리, 최종 equality와 no-op는 제품 UI에서 입증되지 않았다. Android `gradlew`는 `Unable to locate a Java Runtime`로 시작하지 못했고 이 호스트에는 Android SDK/adb도 없다. 그러므로 이 항목의 모든 최종 카운터는 미측정이며 0으로 추정하지 않는다.

## 이번 run의 회귀/build 결과

| 검증 | 결과 |
|---|---|
| Desktop Python | `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`: 622 passed, 1 skipped |
| Focused Persona Sync Python | 42/42 passed |
| Frontend | 98/98 passed |
| Rust | 60/60 passed |
| Frontend production build | PASS |
| Tauri app bundle build | PASS (2 synthetic configurations; unsigned, updater disabled) |
| Sidecar build | PASS; arm64 binary 25,974,656 bytes |
| `git diff --check` | PASS |
| Changed-path secret scan | 34 paths; 5 `api_key` pattern matches in test assignments; all 5 values have test/fixture placeholder shape, confirmed real secret 0 |

Android Python tests were 96/96, but Android JVM/instrumentation and fresh Debug/Release APK builds were blocked by missing JRE, SDK, and adb. APK files already present in the build output are Debug 22,039,732 bytes and unsigned Release 21,453,018 bytes; these are existing artifact sizes, not fresh build results from this run. Tauri app-support paths and fixtures use synthetic data only. Production Persona, production app data, credentials, updater secrets, and signing keys were not accessed.

## Marker

```text
MINDCORE_M7_3_2_1_3 = FAIL / NO-GO
DESKTOP_BASE_COMMIT = 43322b9030a2994a01fa81abd8978fcb9b2a8d9c
ANDROID_BASE_COMMIT = 5de7a96d995d22e4f8735c6e85923dab06a8e9a5
DESKTOP_SCHEMA_VERSION = 24
ANDROID_SCHEMA_VERSION = 24
SCHEMA_FORK_CREATED = NO
PERSONA_SYNC_SCOPE = MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS
DESKTOP_ACTUAL_TAURI_APP_LAUNCHED = YES
DESKTOP_ACTUAL_UI_AUTOMATION = PARTIAL
DESKTOP_REACT_TO_TAURI_IPC = PASS
DESKTOP_TAURI_TO_SYNC_ENGINE = PASS
DESKTOP_PRODUCT_E2E = FAIL
DESKTOP_PAIR_EXPORT_UX = PASS
DESKTOP_PAIR_IMPORT_UX = FAIL
DESKTOP_FINGERPRINT_CONFIRM_UX = FAIL
DESKTOP_TRUSTED_DEVICE_UX = FAIL
DESKTOP_REVOKE_UX = FAIL
DESKTOP_SYNC_EXPORT_UX = FAIL
DESKTOP_SYNC_IMPORT_UX = FAIL
DESKTOP_REPLAY_UX = FAIL
DESKTOP_CONFLICT_INBOX = FAIL
DESKTOP_MUTABLE_RESOLUTION_UX = FAIL
DESKTOP_TOMBSTONE_RESOLUTION_UX = FAIL
DESKTOP_INTEGRITY_CONFLICT_UX = FAIL
UNSUPPORTED_DESKTOP_BACKEND_SAFE = UNVERIFIED
UNSUPPORTED_BACKEND_PARTIAL_MUTATIONS = UNVERIFIED
PRODUCT_UI_CROSS_PLATFORM_PAIRING = FAIL
PRODUCT_UI_DESKTOP_TO_ANDROID_SYNC = FAIL
PRODUCT_UI_DESKTOP_TO_ANDROID_REPLAY = FAIL
PRODUCT_UI_ANDROID_TO_DESKTOP_SYNC = FAIL
PRODUCT_UI_ANDROID_TO_DESKTOP_REPLAY = FAIL
PRODUCT_APPEND_CONVERGENCE = FAIL
PRODUCT_APPEND_NO_OP = FAIL
PRODUCT_MUTABLE_CONFLICT = FAIL
ANDROID_ORIGIN_PRODUCT_RESOLUTION = FAIL
DESKTOP_ORIGIN_PRODUCT_RESOLUTION = FAIL
PRODUCT_RESOLUTION_SYNC = FAIL
KEEP_UPDATED_ITEM_PRODUCT_FLOW = FAIL
DELETE_ON_BOTH_DEVICES_PRODUCT_FLOW = FAIL
PRODUCT_RESOLUTION_VS_RESOLUTION = FAIL
SECOND_PRODUCT_RESOLUTION_CONVERGENCE = FAIL
MULTI_PERSONA_PRODUCT_ISOLATION = FAIL
WRONG_PERSONA_PRODUCT_REJECT = FAIL
FINAL_REAL_PRODUCT_WORKFLOW = FAIL
FINAL_STATE_EQUAL = UNVERIFIED
FINAL_PENDING_NORMAL_CONFLICTS = UNVERIFIED
FINAL_SYNC_RECORD_LOSS = UNVERIFIED
FINAL_SYNC_DUPLICATES = UNVERIFIED
FINAL_NO_OP_MUTATIONS = UNVERIFIED
SYNC_FOUNDATION = TRUE
SECURE_SYNC_CORE = TRUE
REMOTE_APPLY_FOUNDATION = TRUE
DESKTOP_SYNC_COUNTERPART = TRUE
MANUAL_SECURE_TRANSPORT = TRUE
BIDIRECTIONAL_CONVERGENCE_FOUNDATION = TRUE
SYNC_CONFLICT_RESOLUTION = FALSE
PRODUCTION_SYNC_UX = FALSE
PERSONA_SYNC = FALSE
AUTO_SYNC = FALSE
BACKGROUND_SYNC = FALSE
CLOUD_SYNC = FALSE
FULL_COGNITION = TRUE
UI_PRODUCTION_READY = TRUE
AUTONOMY = FALSE
DESKTOP_PYTHON_TESTS = 622/622 (1 skipped)
DESKTOP_SYNC_FOCUSED_TESTS = 42/42
DESKTOP_FRONTEND_TESTS = 98/98
DESKTOP_RUST_TESTS = 60/60
DESKTOP_FRONTEND_BUILD = PASS
DESKTOP_TAURI_BUILD = PASS
DESKTOP_SIDECAR_BUILD = PASS
SECRET_PATTERN_HITS_DESKTOP = 5
CONFIRMED_REAL_SECRETS_DESKTOP = 0
FALSE_POSITIVE_PLACEHOLDERS_DESKTOP = 5
ANDROID_PYTHON_TESTS = 96/96
ANDROID_JVM_TESTS = BLOCKED
ANDROID_INSTRUMENTATION = BLOCKED
ANDROID_DEBUG_BUILD = BLOCKED
ANDROID_RELEASE_BUILD = BLOCKED
ANDROID_DEBUG_APK_SIZE_BYTES = 22039732 (existing; not fresh)
ANDROID_RELEASE_APK_SIZE_BYTES = 21453018 (existing; not fresh)
PLAINTEXT_PERSONA_TRANSPORT = NO (observed pairing artifact contains pairing metadata only)
ANDROID_PRIVATE_SYNC_KEY_EXPORTABLE = UNVERIFIED
DESKTOP_PRIVATE_SYNC_KEY_PLAINTEXT = NO (changed-path scan)
CREDENTIAL_SYNC = NO (no evidence in observed path)
FIRST_FILESYSTEM_ACTION_CORRECT = YES
TOOL_READ_BEFORE_FIRST_ACTION = NO
ANDROID_TOOL_WORKDIR_COMPLIANCE = YES
DESKTOP_TOOL_WORKDIR_COMPLIANCE = YES
WORKDIR_OMITTED_CALLS = 0
SUBDIRECTORY_TOOL_WORKDIR_CALLS = 0
COMMAND_INTERNAL_CHDIR_CALLS = 0
PARENT_TRAVERSAL_USED = 0
FORBIDDEN_PATH_ACCESSED = NO
DIANA_PATH_ACCESSED = NO
PRODUCTION_PERSONA_ACCESSED = NO
PRODUCTION_CREDENTIAL_ACCESSED = NO
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
HISTORICAL_M7_3_2_FAIL_PRESERVED = YES
HISTORICAL_M7_3_2_1_FAIL_PRESERVED = YES
HISTORICAL_M7_3_2_1_1_FAIL_PRESERVED = YES
HISTORICAL_M7_3_2_1_2_FAIL_PRESERVED = YES
DESKTOP_IMPLEMENTATION_COMMIT = NONE
DESKTOP_FINAL_HEAD = NONE (dirty; base 43322b9030a2994a01fa81abd8978fcb9b2a8d9c)
ANDROID_IMPLEMENTATION_COMMIT = NONE
ANDROID_FINAL_HEAD = NONE (dirty; base 5de7a96d995d22e4f8735c6e85923dab06a8e9a5)
NEXT_DECISION = SYNC_PRODUCT_HOTFIX / M7.4 NO-GO
```
