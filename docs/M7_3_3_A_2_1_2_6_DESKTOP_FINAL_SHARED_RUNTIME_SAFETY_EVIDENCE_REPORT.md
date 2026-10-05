# MindCore M7.3.3-A.2.1.2.6 — Desktop Final Shared Runtime Safety Evidence

날짜: 2026-10-02 (Asia/Seoul). 동일 명세 재요청에 대한 **두 번째 실행**이다.

## 판정 요약

`.2.6 = FAIL`. 실제 Desktop foreground turn에서 synthetic provider를 1회 호출하고 프로세스를 중단했으나, `provider_started` 경계에 해당하는 durable stage가 존재하지 않았다. 같은 실패를 두 개의 독립 실행에서 재현했다. Phase K early-stop을 적용하여 전체 회귀와 Phase N fresh build는 실행하지 않았다.

첫 실행에서 실패했던 concurrency는 호스트 proxy와 native Desktop 호출을 별도 프로세스로 분리한 뒤 두 번 PASS했다. 제품 DB adapter, schema, production runtime은 변경하지 않았다. 첫 실행 FAIL 보고서는 `attempt2-start`에 SHA256 검증 가능한 원문으로 보존했고 아래에 재실행 이력을 명시했다. 과거 milestone verdict는 그대로 유지한다.

LOCAL_ONLY_PERSONA는 한 기기의 authoritative local DB, SHARED_PERSONA는 Desktop과 Android가 직접 접근하는 ONE authoritative DB이다. Legacy peer/replica sync는 deprecated architecture로 유지한다. **IMPLEMENTED != ACCEPTANCE-PROVEN**.

## [Summary]

```text
M7.3.3-A.2.1.2.6 = FAIL
M7.3.3-A.2.1.2.6.1 = PASS
TECHNICAL_ACCEPTANCE = FAIL
SHARED_RUNTIME_SAFETY = PARTIAL
PROCEDURAL_WARNINGS = 1 (현재 사용자 계정의 실제 clone 경로 사용)
CRITICAL_VIOLATIONS = 0
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
FULL_COGNITION = TRUE (기존 acceptance 유지; 이번 안전성 closure와 별개)
```

## [Repository]

```text
ANDROID_START_HEAD = 332395077a9d77a5c00d0312e4fef7d740896cf2
ANDROID_FINAL_HEAD = 332395077a9d77a5c00d0312e4fef7d740896cf2
DESKTOP_START_HEAD = f2eef62dac416729891f54887f13b95821977c4a
DESKTOP_FINAL_HEAD = f2eef62dac416729891f54887f13b95821977c4a
DIRTY_WORK_PRESERVED = YES
```

| 저장소 | 실제 경로 | branch |
|---|---|---|
| Desktop | `/Users/noseunghudong-alibujang/Developer/mindcore-desktop` | `backup/mindcore-desktop-current-2026-09-30` |
| Android | `/Users/noseunghudong-alibujang/Developer/mindcore-android` | `backup/mindcore-android-current-2026-09-30` |

이번 실행은 dirty 상태에서 시작했다. Desktop tracked test 2개, untracked driver/보고서 2개, Android tracked debug fixture 1개 및 untracked 보고서 1개가 이미 있었다. 6개 모두 `/Users/noseunghudong-alibujang/Developer/mindcore-android/.toolchain/m7333a2126-evidence/attempt2-start/manifest.json`과 원문 snapshot으로 보존했고 SHA256이 일치함을 확인했다. Android fixture와 Desktop primitive test의 기존 변경은 이번 실행에서 변경하지 않았다. 누적 harness/driver를 보완하고 이 두 보고서를 갱신했다. Production source/schema 변경, destructive Git 명령, commit은 없다.

## [Harness]

```text
CROSS_DEVICE_RECOVERY_HARNESS = PASS (프로세스 분리 보정 후 sanity)
DESKTOP_RECOVERY_CALLABLE = PASS
ANDROID_RECOVERY_CALLABLE = PASS
PROVIDER_OBSERVER = PASS (synthetic 호출의 turn/origin/count 기록)
COGNITION_OBSERVER = PASS (accepted actual Android recovery assertion 범위)
CROSS_DEVICE_BARRIER = PASS
HARNESS_REGRESSION = SAME_PROCESS_STARVATION_REPRODUCED_AND_ISOLATED
```

승인된 `.2.5`의 SqldLifecycle, LocalHranaMetadataProxy, CrossDeviceBarrier, SyntheticProviderObserver, CognitionExecutionObserver 및 실제 D/A recovery entry point를 재사용했다. Android는 host Python import가 아닌 실제 arm64 emulator `emulator-5560`의 instrumentation/Chaquopy process에서 실행했다.

이번 primitive suite: **4 passed in 11.14s**. 확인 실행 Android instrumentation: shared smoke **2/2**, accepted recovery/outage/barrier sanity **3/3**, concurrent append **1/1 PASS**. Android same-owner recovery는 실제 Relationship claim/execute/complete를 assertion했다. 해당 test는 numeric observer 결과 전체를 로그에 export하지 않으므로 숫자는 임의로 기록하지 않는다.

Desktop baseline recovery는 recovered_turns=0이며 stage claim/execute/complete 및 mutation observer가 모두 0이다. 이 수치는 provider-interrupted turn의 recovery 결과가 아니라 baseline 시점의 호출 결과이다.

### 하네스 진단 및 보정 이력

| 실행 | D 행 / 예상 | A 행 / 예상 | 최대 host heartbeat gap | 결과 |
|---|---:|---:|---:|---|
| 기존 첫 실행 run4 | 10/10 | 3/20 | 미측정 | transaction timeout으로 FAIL |
| 이번 same-process 진단 | 10/10 | 2/20 | 4774.164ms | transaction timeout으로 FAIL |
| 이번 isolated 비교 | 10/10 | 20/20 | 331.115ms | concurrency PASS, provider boundary FAIL |
| 이번 confirmation | 10/10 | 20/20 | 362.370ms | concurrency PASS, provider boundary FAIL |

동일 프로세스에서는 native libSQL 쓰기와 Android HTTP proxy가 같은 Python interpreter에서 실행됐다. 이때 약 4.77초의 host stall과 `TRANSACTION_TIMEOUT`, 이어지는 `cannot rollback - no transaction is active`를 관찰했다. Desktop append를 child process로 옮기면 timeout이 사라지고 10쌍 쓰기가 완료됐다. 따라서 앞선 concurrency 실패를 제품 회귀로 확정했던 판단은 이번 비교 evidence로 정정한다. **Native 호출의 GIL 점유라는 세부 원인은 추론**이며 native stack profiler로 확정하지 않았다. Proxy request timing은 handler에 진입한 이후부터 측정하므로 request 수신 이전 stall을 포함하지 않는다.

보정은 기존 실제 Desktop repository.create_message 경로의 프로세스 격리뿐이다. SQL retry나 production adapter 변경이 아니다. driver의 기본값은 격리이며 `--diagnostic-same-process`는 해당 진단 재현용이다. 각 비교 실행은 독립 synthetic DB를 사용했다. 최종 acceptance 확인 실행의 모든 단계는 아래 **한 DB storage**에서 수행했고, 서로 다른 실행의 상태를 합쳐 PASS로 간주하지 않았다.

## [Shared DB]

```text
SCHEMA_VERSION = 24
PERSONA_ID = 7f6fa614-3df8-4a78-97a2-31e4a2f7c850
DESKTOP_DEVICE_ID = 98e27b8c-071a-4bf2-8f29-8417178b80a7
ANDROID_DEVICE_ID = f789aed2-5761-44b4-a076-532a45bacf2c
PERSONA_ID_EQUAL = YES
ACTUAL_SHARED_DB_SMOKE = PASS
CONCURRENT_SHARED_WRITES_REGRESSION = PASS
CONCURRENT_LOSS = 0
CONCURRENT_DUPLICATES = 0
STABLE_ID_COLLISIONS = 0
UNRECOVERED_STREAM_EXPIRED = 0 observed in executed concurrency
```

최종 DB storage: `/private/tmp/mindcore-m7333a2126-attempt2-confirmation/shared.sqld`.
Conversation: `762163f4-779f-406c-b92e-3899c2e042e7`.
sqld 0.24.32, native Desktop libsql 0.1.11, schema24를 사용했다.

D→A, A→D message와 동일 Persona attach를 실제 runtime으로 확인했다. 10 paired append의 Desktop 예상/실제는 10/10, Android는 20/20이다. 기존 fixture의 Android 10 chat turns는 user+assistant 20행을 만든다. 두 participant의 tag가 붙은 30개 ID가 모두 다르며 role/content 중복도 0이다. 전체 synthetic snapshot은 message 37, turn 16, stage 162, Memory 2, Relationship log 5행이다. 이 전체 행 수는 30행 concurrency 대상과 구별한다.

## [Android Outage]

```text
DB_ACTUALLY_STOPPED = YES (accepted harness sanity)
OUTAGE_ANDROID_MESSAGE_BLOCKED = NOT_PROVEN_FOR_STRICT_PHASE_C
OUTAGE_ANDROID_COGNITION_BLOCKED = NOT_PROVEN_FOR_STRICT_PHASE_C
ANDROID_LOCAL_FALLBACK_WRITES = NOT_MEASURED_FOR_FINAL_ACCEPTANCE
ANDROID_PERSONA_FORKS = NOT_MEASURED_FOR_FINAL_ACCEPTANCE
FAILED_OUTAGE_WRITES_RESURRECTED = NOT_MEASURED
DB_RESTARTED_SAME_STORAGE = YES
RECONNECT_SAME_PERSONA = NOT_PROVEN_FOR_FULL_OUTAGE_SCENARIO
POST_RECONNECT_ANDROID_TO_DESKTOP = NOT_PROVEN_FOR_FULL_OUTAGE_SCENARIO
```

Accepted sanity에서 실제 sqld process를 SIGTERM으로 중단하고 같은 storage path로 재시작했다. Outage 중 실제 Android prepare_send 및 cognition recovery 경로가 호출됐고 host에서 두 URLError를 기록했다. Restart 이후 concurrency가 정상 실행됐다. 다만 기존 instrumentation assertion은 blocked boolean **필드 존재** 및 restart를 확인하며, 전체 Phase C의 state S, zero fallback/fork, failed-write resurrection=0 및 R_A 전달을 검증하지 않는다. 이번에는 provider boundary를 우선 진단하는 순서로 실행했으며 strict Phase C 전체를 수행하지 않았다. Limited sanity를 outage safety PASS로 승격하지 않는다.

## [Provider Ownership]

```text
DESKTOP_PROVIDER_INVOCATIONS = 1 (각 T_D interruption probe)
ANDROID_REPLAY_OF_DESKTOP_PROVIDER = NOT_RUN
ANDROID_PROVIDER_INVOCATIONS = NOT_RUN (T_A ownership probe)
DESKTOP_REPLAY_OF_ANDROID_PROVIDER = NOT_RUN
CROSS_DEVICE_PROVIDER_REPLAY = NOT_PROVEN
DUPLICATE_ASSISTANT_OUTPUT = NOT_PROVEN_FOR_BIDIRECTIONAL_OWNERSHIP
SAME_OWNER_RECOVERY_SANITY = PASS (accepted Android Relationship recovery)
```

### 실제 실패한 invariant 및 durable state

```text
FAILURE_CLASS = CODE FAILURE (Desktop foreground durability contract gap)
ERROR = DESKTOP_FOREGROUND_PROVIDER_STARTED_NOT_DURABLE
turn_id = 7cb12b2c-8828-48b6-9128-b83950d20643
origin_device_id = 98e27b8c-071a-4bf2-8f29-8417178b80a7
provider_invocation_count = 1
interrupted_process_exit = 91
user_message_id = 7cb12b2c-8828-48b6-9128-b83950d20643
assistant_message_id = null
turn.status = pending
core_completed_at = null
chat_turn_stages for T_D = []
```

실제 ChatTurnCoordinator.execute와 TurnDurability.begin_turn을 통해 user message 및 turn을 생성했다. Provider seam만 승인된 deterministic synthetic observer로 주입했다. 첫 provider 호출 내부에서 실제 DB ledger를 읽어 기록하고 `os._exit(91)`로 Desktop child process를 중단했다. Durability row를 직접 생성하거나 failure handler를 mock하지 않았다. Child 종료 뒤 host의 실제 TursoPool inspection에서도 같은 pending turn, assistant 없음, stage 없음이 확인됐다.

Isolated 비교 실행의 별도 T_D=`5b67a284-c7e9-4af0-ade8-0b4e1a486491`에서도 호출 1회, exit91, stages=[]로 동일 실패했다. **관찰한 실패는 durable provider_started 누락**이며 실제 Android가 provider를 replay했다는 결과가 아니다. Cross-device replay count를 0으로 채우지 않는다.

Relevant production path: Desktop `TurnDurability.begin_turn`은 user/turn만 저장하고, foreground coordinator는 context 이후 generate_reply를 직접 호출한다. Post-cognition ledger는 `complete_core`에서 생성된다. context_prepare/provider_generate/assistant_persist ledger는 현재 Desktop의 명시적 proactive turn 경로에만 존재한다. Foreground pending turn은 `incomplete_turn_ids`의 core_completed_at 조건에도 포함되지 않는다. Provider 시작 및 interruption ambiguity에 대한 foreground durable 계약이 이번 요구 조건을 충족하지 못한다.

명세 §57의 early-stop을 적용해 이 실패 지점에서 멈췄다. Hotfix는 허용 사항이며 이번 실행에서는 production durability semantics를 변경하지 않았다. Follow-up에서 schema24의 foreground provider 경계와 ambiguity 처리를 최소 범위로 보완하고 해당 actual interruption부터 다시 검증해야 한다.

## [Post Cognition]

```text
DESKTOP_ORIGIN_COMPLETED_STAGES = NOT_MEASURED_FOR_C_D
DESKTOP_COMPLETED_COGNITION_RERUN_ON_ANDROID = NOT_RUN
ANDROID_ORIGIN_COMPLETED_STAGES = NOT_MEASURED_FOR_C_A
ANDROID_COMPLETED_COGNITION_RERUN_ON_DESKTOP = NOT_RUN
DUPLICATE_MEMORY_MUTATIONS = NOT_MEASURED_FOR_REQUIRED_SCENARIOS
DUPLICATE_RELATIONSHIP_MUTATIONS = NOT_MEASURED_FOR_REQUIRED_SCENARIOS
DUPLICATE_OTHER_COGNITION_MUTATIONS = NOT_MEASURED_FOR_REQUIRED_SCENARIOS
DUPLICATE_STAGE_COMPLETIONS = NOT_MEASURED_FOR_REQUIRED_SCENARIOS
DUPLICATE_POST_COGNITION = NOT_PROVEN
```

Baseline에는 실제 cognition 및 158개 completed stage가 있으나 양방향 C_D/C_A rerun 방지 증거와 구별한다. Full stable ID, attempt_count, started_at, completed_at은 `final-durable-state.json`에 보존했다.

Memory ID 예: `a1cc104b-d0bf-4be6-8427-a7f75650bf33`, `7a33dd0f-6b17-4f5e-a1e6-a661beee9246`.
Relationship source experience ID 예: `f9b17919-2823-4eef-946d-a1759f4e0f68`.
추가 cognition domain의 exactly-once 검증과 replay 전후 ID 비교는 미실행이다.

## [Stage Claim Race]

```text
STAGE_RACE_ITERATIONS = 0
DESKTOP_CLAIM_ATTEMPTS = NOT_RUN
ANDROID_CLAIM_ATTEMPTS = NOT_RUN
DESKTOP_STAGE_EXECUTIONS = NOT_RUN
ANDROID_STAGE_EXECUTIONS = NOT_RUN
CROSS_DEVICE_STAGE_CLAIM_RACE = NOT_PROVEN (acceptance FAIL)
DURABLE_EXACTLY_ONCE = NOT_PROVEN (acceptance FAIL)
```

Accepted barrier의 두 participant release는 PASS지만 실제 pending stage에 대한 최소 10회 race는 실행하지 않았다. Baseline append의 wall-clock 시작 시간은 기존 smoke의 방법이며 Phase I claim race의 deterministic barrier 증거로 대체하지 않는다.

## [Durability]

```text
TURN_DURABILITY_SHARED_DB = FAIL (요구 계약 미성립)
PROVIDER_STARTED_SEMANTICS = FAIL (Desktop foreground)
STAGE_CLAIM_SEMANTICS = NOT_PROVEN_FOR_CROSS_DEVICE_RACE
STAGE_COMPLETE_SEMANTICS = NOT_PROVEN_FOR_CROSS_DEVICE_RACE
```

## [Focused Safety]

```text
FOCUSED_TESTS = 10/11 executed checks
FOCUSED_FAILURES = 1
FOCUSED_ERRORS = 0 runner errors (provider boundary assertion 1 FAIL)
FOCUSED_CORE_SKIPS = 5 required safety scenarios unexecuted / NOT_PROVEN
RUNNER_TEST_SKIPS = 0
REQUIRED_CORE_SAFETY_SCENARIOS_ACCEPTED = 0/6
REQUIRED_CORE_SAFETY_SCENARIOS_FAILED = 1 (Desktop provider_started prerequisite)
REQUIRED_CORE_SAFETY_SCENARIOS_NOT_PROVEN = 5
```

10 PASS는 primitive4 + shared smoke2 + accepted harness3 + concurrency1이다. 나머지 1 FAIL은 실제 Desktop provider boundary check다. 10/11은 전체 `.2.6` acceptance coverage를 의미하지 않는다. 핵심 safety 6개는 PASS0, FAIL1, NOT_PROVEN5로 closure 실패이다. Phase F same-owner sanity는 accepted helper 범위에서 PASS이다. 필수 시나리오 미실행을 환경 skip PASS로 취급하지 않는다.

## [Regression]

```text
ANDROID_PYTHON = NOT_RUN (early-stop)
ANDROID_JVM = NOT_RUN (early-stop)
ANDROID_INSTRUMENTATION = focused 6 passed; full NOT_RUN
ANDROID_NEW_REGRESSIONS = NOT_MEASURED (full suite 미실행)
DESKTOP_PYTHON = primitive 4 passed; full NOT_RUN
DESKTOP_FRONTEND = NOT_RUN
DESKTOP_RUST = NOT_RUN
NEW_REGRESSIONS = NOT_MEASURED
```

현재 HEAD의 Desktop provider boundary gap을 확인했으며 새 production 변경으로 유입된 회귀는 아니다. 앞선 concurrency 실패는 이번 비교로 하네스 프로세스 배치 문제로 분류한다. 제품 NEW_REGRESSIONS=0을 full suite 없이 주장하지 않는다.

## [Build]

```text
ANDROID_DEBUG_FRESH_BUILD = NOT_RUN (Phase N gate)
ANDROID_RELEASE_FRESH_BUILD = NOT_RUN
ANDROID_DEBUG_APK_SIZE_BYTES = 22137029 (이전 실행의 instrumentation 준비용 APK)
ANDROID_RELEASE_APK_SIZE_BYTES = NOT_RUN
ANDROID_TEST_APK_SIZE_BYTES = 1825845 (이전 실행의 준비용 APK)
DESKTOP_FRONTEND_BUILD = NOT_RUN
DESKTOP_TAURI_BUILD = NOT_RUN
DESKTOP_SIDECAR_BUILD = NOT_RUN
```

이번 실행은 앞서 준비한 Debug/test APK로 instrumentation을 실행했다. 이를 fresh build로 보고하지 않는다. 프로젝트 manifest의 minSdk24/targetSdk36는 확인했지만 새로운 Release artifact의 arm64/16KiB/cleartext/test hook 검사는 수행하지 않았다.

## [Security]

```text
SECRET_PATTERN_HITS = 1
FALSE_POSITIVES = 1 (명시적 synthetic test token)
UNRESOLVED_SECRET_HITS = 0
CONFIRMED_REAL_SECRETS_ANDROID = 0 (changed test/debug scope)
CONFIRMED_REAL_SECRETS_DESKTOP = 0 (changed test/debug scope)
TEST_PROVIDER_HOOK_IN_RELEASE = NOT_RUN
RECOVERY_TEST_HOOK_IN_RELEASE = NOT_RUN
FAILURE_INJECTION_IN_RELEASE = NOT_RUN
TEST_DB_ENDPOINT_IN_RELEASE = NOT_RUN
PRODUCTION_SECURITY_WEAKENED = NO
```

누적 변경된 test/debug source4개를 credential literal/private key/provider key/GitHub token/JWT pattern으로 검사했다. 실제 값은 보고서와 scanner 출력에 포함하지 않는다. Observer는 error code/type, SQL 문장 앞부분 및 timing만 기록하며 token/header/baton/SQL argument는 기록하지 않는다. Synthetic-only environment로 provider 실행을 격리했고 외부 provider call은 없었다. Production credential, private Persona, updater secret, permanent signing key 및 다른 사용자 SDK/cache를 접근하지 않았다. Fresh release 미실행으로 release hook 부재를 NO라고 단정하지 않는다.

## [Capabilities]

```text
SHARED_PERSONA_DB = TRUE (실제 baseline)
PERSONA_SHARED_RUNTIME = TRUE
SAME_PERSONA_IDENTITY = TRUE
CONCURRENT_SHARED_WRITES = TRUE (이번 격리 재현 PASS)
SHARED_DB_OUTAGE_SAFE = PARTIAL
CROSS_DEVICE_PROVIDER_OWNERSHIP = NOT_PROVEN
POST_COGNITION_EXACTLY_ONCE = NOT_PROVEN
TURN_DURABILITY_SHARED_DB = FALSE (이번 최종 계약 FAIL)
SHARED_RUNTIME_SAFETY = PARTIAL
DEVICE_PERSONA_REPLICA_REQUIRED = FALSE
MANUAL_SYNC_FILE_REQUIRED = FALSE
FULL_COGNITION = TRUE (기존 acceptance 유지)
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
```

기존 `.2.2=PASS`, `.2.3=FAIL`, `.2.4=FAIL/NOT_PROVEN`, `.2.5=PASS(harness)`를 포함한 역사적 verdict는 수정하지 않았다. Remote authenticated DB 및 Keychain acceptance를 이번 local sqld 결과로 신규 입증하지 않는다.

## [Commits]

```text
ANDROID_IMPLEMENTATION_COMMIT = NONE
ANDROID_REPORT_COMMIT = NONE
ANDROID_FINAL_HEAD = 332395077a9d77a5c00d0312e4fef7d740896cf2
DESKTOP_IMPLEMENTATION_COMMIT = NONE
DESKTOP_REPORT_COMMIT = NONE
DESKTOP_FINAL_HEAD = f2eef62dac416729891f54887f13b95821977c4a
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
```

## [Next]

```text
NEXT_DECISION = SHARED_RUNTIME_SAFETY_HOTFIX
```

Schema24의 Desktop foreground provider_started 및 interruption ambiguity 경계를 보완한 뒤 Phase D actual runtime probe부터 재실행한다. 이후 strict Android outage, 양방향 provider ownership/cognition rerun 방지, ≥10회 barrier claim race, TurnDurability 전체를 검증해야 한다. 모두 PASS일 때만 regression→fresh builds→release isolation 검사를 진행한다. PC v0.4.0 + Android release closure는 아직 불가하다.

## 실행 명령과 evidence 보존

Desktop focused driver(격리가 기본값):

```sh
.venv/bin/python -m tests.run_m7333a2126_safety_evidence \
  --android-root /Users/noseunghudong-alibujang/Developer/mindcore-android \
  --evidence /tmp/mindcore-m7333a2126-attempt2-confirmation
.venv/bin/python -m pytest tests/test_m7333a2125_recovery_harness.py -q
```

Driver는 credential 환경변수를 전달하지 않는 env-i 환경에서 실행했다. Instrumentation 세 클래스는 actual `adb -s emulator-5560 shell am instrument -w -r`로 opt-in arguments와 함께 실행했다. Debug test package는 `com.luskacat.mindcore.android.test/androidx.test.runner.AndroidJUnitRunner`이다.

영구 evidence root: `/Users/noseunghudong-alibujang/Developer/mindcore-android/.toolchain/m7333a2126-evidence`.

- `attempt2-start/`: 이전 dirty source/보고서 6개 원문 및 SHA256 manifest.
- `run1`~`run4`: 첫 실행 evidence 보존.
- `attempt2-same-process/`: timeout 재현, durable state, timing.
- `attempt2-isolated/`: concurrency PASS 및 첫 actual provider boundary FAIL.
- `attempt2-confirmation/`: 최종 재현 로그, DB storage copy, raw durable state, provider boundary/counter, concurrency row ID 목록.
- `attempt2-comparison.json`, `attempt2-primitives.log`, `attempt2-security-scan.json`, `attempt2-preservation-check.json`.

최종 evidence의 `result.json`, `desktop-provider-start.json`, `final-durable-state.json`은 provider failure의 서로 일치하는 snapshot을 담는다. Driver finally에서 sqld/proxy/controller를 종료하고 모든 생성 adb reverse를 제거했다. 직접 만든 synthetic emulator도 종료했다. 이전 및 현재 evidence는 삭제하지 않았다.

## R1 재개 실행 — 2026-10-04

이 절은 위의 original `.2.6 = FAIL` 이력과 `.2.6.1 = PASS` 결과를 덮어쓰지 않는 별도 재개 기록이다. `.2.6.1` accepted 변경은 다음 local-only commit으로 먼저 고정했다. Pre-existing dirty harness/test 작업은 별도로 보존했다.

```text
PREEXISTING_DIRTY_WORK_PRESERVED = YES
DESKTOP_2_6_1_IMPLEMENTATION_COMMIT = 48341f09307729fc83620153b28678292f361a57
DESKTOP_2_6_1_REPORT_COMMIT = 7d3d4e62b4d8e4157ce64fe98c749d9ecfb756c1
DESKTOP_START_HEAD = 7d3d4e62b4d8e4157ce64fe98c749d9ecfb756c1
DESKTOP_FINAL_HEAD = 7d3d4e62b4d8e4157ce64fe98c749d9ecfb756c1
ANDROID_2_6_1_IMPLEMENTATION_COMMIT = 4692ff5a456264e42588951fbb6befd6eba485ac
ANDROID_2_6_1_REPORT_COMMIT = c8ba3a3b08df15074a66725f69e52b53a5f97bef
ANDROID_START_HEAD = c8ba3a3b08df15074a66725f69e52b53a5f97bef
ANDROID_FINAL_HEAD = c8ba3a3b08df15074a66725f69e52b53a5f97bef
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
```

### R1 safety evidence

한 schema24 synthetic DB, Persona `7f6fa614-3df8-4a78-97a2-31e4a2f7c850`, Desktop D와 Android A에서 실제 runtime/accepted `.2.5` recovery entry를 사용했다.

- Baseline shared attach, 양방향 message smoke와 10 paired write는 PASS: Desktop 10/10, Android 20/20, loss 0, duplicate 0, ID collision 0, unrecovered `STREAM_EXPIRED` 0.
- Android-origin provider interruption은 유효 반복 5회 수행했다. 매회 Android provider observer는 1회 호출했고, Desktop 실제 recovery는 provider replay 0회 및 duplicate assistant 0회를 냈다. 소유 Android가 재연결되면 해당 turn은 `core_failed / PROVIDER_INDETERMINATE`로 기록되고 assistant는 없다. Desktop recovery 시점에는 일부 turn이 `pending` 및 provider stage `running`으로 그대로 남았으므로 이를 완료 복구로 간주하지 않는다.
- 실제 sqld 중단에서 Android message와 cognition durable write는 fail-closed됐다. 같은 storage path/Persona로 재시작했고, outage 전후 message/turn/Memory/Relationship/Episode 수가 바뀌지 않았으며 fallback DB/write, Persona fork, failed write resurrection은 0이다. 이후 Android에서 완료한 `R1 ANDROID PERSONAL EVENT 02` turn `c5f78401-8267-4115-8fcb-62d6e8717f0f`와 assistant가 Desktop의 같은 DB query에서 확인됐다.
- Desktop-origin completed cognition turn `38b51288-8b2f-4d22-bc8d-56f60cdc1a45`는 23 stages가 각각 attempt 1로 완료됐고 Memory `9acaa2a3-f3a0-4a7b-817f-dc0e4028c541`, Relationship `e2c728ee-5dd9-49e7-ab5e-b4a4d8932a8f`, Episode `c3a8f50a-d60a-4c9b-80e5-fd31dc93763f`를 만들었다. Android actual recovery 전후 stage attempt, durable IDs 및 row count가 같았다.
- Android-origin completed cognition turn `521f1bad-2739-4e13-8caa-57e22672c0f9`는 14 stages가 각각 attempt 1로 완료됐고 Memory `a746d197-397f-434b-be62-dbd24647b7bc`, Relationship `833e949f-c622-43e5-9251-17e812639e50`, Episode `3144740a-45e3-43cb-bdc2-052dee0d196e`를 만들었다. Desktop actual recovery 전후 stage attempt, IDs 및 row count가 같았다.
- 실제 cross-device barrier stage claim race를 10회 수행했다. Android는 회당 claim/execute/complete 1회, Desktop은 회당 실제 claim 시도 1회 후 CAS에서 패했다. `relationship` stage completion 및 durable Relationship mutation은 매회 정확히 1건이었다. 상세 record는 `mindcore-desktop/.toolchain/m7333a2126-evidence/attempt-r1-baseline3/stage-race-10.json`에 있다.

```text
DESKTOP_ORIGIN_REPLAY_ON_ANDROID = 0
ANDROID_ORIGIN_REPLAY_ON_DESKTOP = 0
ANDROID_ORIGIN_VALID_ITERATIONS = 5
FOCUSED_TESTS = 5/5 required scenarios
FOCUSED_FAILURES = 0
FOCUSED_ERRORS = 0
FOCUSED_CORE_SKIPS = 0
OUTAGE_FAIL_CLOSED = PASS
OUTAGE_FALLBACK_FORK_RESURRECTION = 0
POST_RECONNECT_ANDROID_TO_DESKTOP = PASS
DESKTOP_COMPLETED_COGNITION_RERUN_ON_ANDROID = 0
ANDROID_COMPLETED_COGNITION_RERUN_ON_DESKTOP = 0
STAGE_RACE_ITERATIONS = 10
DURABLE_EXACTLY_ONCE = PASS (Relationship stage result)
```

Provider replay와 cognition duplicate 방지는 입증됐다. 다만 provider-started Android turn은 Desktop 복구 시점에 재생되거나 완료되지 않고 원래 소유 Android가 돌아온 뒤 명시적 `PROVIDER_INDETERMINATE` 실패로 끝났다. 따라서 이번 재개 evidence는 cross-device successful turn completion/recovery까지 증명하지 못한다.

### R1 regressions, builds, security

```text
DESKTOP_PYTHON = 650 passed, 3 skipped, 380 subtests passed, 1 warning
DESKTOP_FRONTEND = 98 passed
DESKTOP_RUST = 60 passed
DESKTOP_CARGO_CHECK = PASS
ANDROID_PYTHON = 102 passed
ANDROID_JVM = 9 passed
ANDROID_INSTRUMENTATION = 75 cases, 37 passed, 38 skipped, 0 failures (current synthetic AVD app data cleared before final run)
NEW_REGRESSIONS = 0 (clean full-suite rerun)
DESKTOP_FRONTEND_BUILD = PASS
DESKTOP_SIDECAR_BUILD = PASS (28,712,496 bytes)
DESKTOP_TAURI_BUILD = PASS (--no-bundle; 17,480,704 bytes)
ANDROID_DEBUG_BUILD = PASS (21,580,912 bytes)
ANDROID_RELEASE_BUILD = PASS (unsigned, 21,485,786 bytes)
ANDROID_RELEASE_MIN_TARGET_ABI = 24 / 36 / arm64-v8a
ANDROID_RELEASE_16K_ALIGNMENT = PASS
ANDROID_RELEASE_CLEARTEXT = DISABLED
ANDROID_LINT = 5 errors / 15 warnings (기존 오류 수정하지 않음)
CONFIRMED_REAL_SECRETS = 0
TEST_HOOK_IN_RELEASE = NO
FAILURE_INJECTION_IN_RELEASE = NO
PRODUCTION_SECURITY_WEAKENED = NO
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
```

초기 AVD의 기존 synthetic test data 때문에 instrumentation 첫 run에서 6 failures가 있었지만, target/test package data를 비운 뒤 같은 전체 suite는 0 failures로 완료됐다. 두 번째 legacy shared DB smoke는 message list 제한을 전체 개수로 가정해 assertion이 실패했다. 별도 actual Android completion과 Desktop의 shared DB query는 R_A를 확인했다. 이 진단은 test fixture limitation으로 기록하며 safety PASS 근거로 쓰지 않았다. 광범위 secret scan의 단일 문자열 표식은 `.venv` 내 `cryptography` parser source였고, 변경 scope 및 acceptance evidence scan은 0 hits였다.

### 최종 R1 판정

```text
M7.3.3-A.2.1.2.6 = FAIL
M7.3.3-A.2.1.2.6.1 = PASS
TECHNICAL_ACCEPTANCE = FAIL
SHARED_RUNTIME_SAFETY = PARTIAL
TURN_DURABILITY_SHARED_DB = FAIL (successful cross-device completion NOT_PROVEN)
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
CRITICAL_VIOLATIONS = 0
PROCEDURAL_WARNINGS = 5
POST_COGNITION_EXACTLY_ONCE = TRUE
CROSS_DEVICE_PROVIDER_OWNERSHIP = TRUE
SHARED_DB_OUTAGE_SAFE = TRUE
NEXT_DECISION = SHARED_RUNTIME_SAFETY_HOTFIX
```

R1에서 production source/schema는 변경하지 않았고, `.2.6.1` 외 implementation/report commit도 만들지 않았다. 기존 commit은 local에만 있으며 report 및 test/debug harness evidence는 현재 working tree에 보존돼 있다.

절차상 warning 5건: (1) 기존 synthetic data가 남은 AVD의 첫 connected full run에서만 실패가 관찰되어 app/test data clear 후 재실행했다. (2) 한 번의 직접 `am instrument` 진단에서 class filter를 빠뜨려 opt-in harness까지 포함된 suite가 실행됐다. (3) legacy `ZM7333A211` smoke의 list/count assertion은 두 번 실패했지만 별도 actual runtime write와 Desktop DB observation은 성공했다. (4) 첫 race 검증은 의도적으로 미완료 stage가 남는 probe에 전체-stage 완료를 요구해, 대상 Relationship claim/result 조건으로 재실행했다. (5) 넓은 secret-pattern scan의 단일 match는 `.venv` cryptography parser 코드였고 changed-scope/evidence scan은 0이다. Android lint는 warning이 아니라 별도 결과인 5 errors/15 warnings이며 수정하지 않았다.
