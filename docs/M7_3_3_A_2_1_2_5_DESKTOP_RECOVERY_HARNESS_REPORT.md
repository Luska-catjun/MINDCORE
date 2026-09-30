# MindCore Desktop M7.3.3-A.2.1.2.5 — Shared Persona Recovery Harness

**결과: PASS**
**Technical acceptance: PASS**
**Date:** 2026-09-30 (Asia/Seoul)

## 요약

Desktop/Android 공용 schema-24 synthetic Persona DB를 대상으로 각 플랫폼의 실제 recovery entry point를 호출하고 관찰할 harness를 추가했다. Desktop의 startup recovery와 Android의 shared bind recovery를 같은 DB와 Persona ID에 연결했고, deterministic provider observer, cognition stage counters, 두 플랫폼 barrier, sqld lifecycle controller, Android outage write/cognition invocation을 구현했다.

이번 PASS는 다음 단계의 safety 검증을 실행할 수 있다는 뜻이다. Provider replay=0, cognition exactly-once, outage safety 최종 PASS를 의미하지 않는다. 이들은 M7.3.3-A.2.1.2.6의 실제 시나리오에서 판정한다.

## Desktop 실제 진입점

제품 startup은 `app.main` lifespan에서 `app.services.turn_recovery.recover_incomplete_turns(pool)`를 예약한다. 이 함수가 `resume_turn`을 거쳐 `TurnDurability.run_stage`와 자동 허용된 post-cognition owners를 실행한다. `tests.m7333a2125_recovery_harness.run_desktop_recovery_once`는 schema version 24와 `mindcore_persona_id`를 실제 Desktop `TursoPool`로 확인한 다음 이 recovery function을 직접 한 번 호출한다.

Desktop cognition observer는 test-only patch로 실제 `TurnDurability.run_stage` 실행을 감싸며 claim 성공 후 execute와 complete counter를 기록한다. `inspect_durable_turn`은 같은 shared DB에서 turn/stage rows, Memory, Relationship, Episode count를 읽어 후속 safety test가 결과를 비교할 수 있게 한다.

## 공유 하네스와 Android counterpart

- `tests/run_m7333a2125_recovery_harness.py`가 same-storage `sqld`를 실행하고, readiness 확인 후 종료·재시작한다.
- Android test adapter용 Hrana metadata proxy는 SQL 요청/결과와 baton을 실제 sqld에 전달하고 `base_url` affinity 메타데이터만 제거한다. Desktop 및 Android production HTTP/TLS 정책은 바꾸지 않았다.
- loopback control server는 Android의 `adb reverse`를 통해서만 도달한다. Android 측 `runtime.cross_device_recovery_harness`는 실제 `runtime.chat.prepare_send`, `TurnDurability.recover_foreground_turns`, `CoreCognitionRuntime.recover`를 호출한다.
- Android instrumentation은 Desktop과 같은 schema24 Persona를 확인하고 synthetic turn 및 Relationship stage를 준비한 뒤 actual Android recovery를 호출한다. Claim/execute/complete counter와 durable stage 완료를 검사한다.
- HTTP barrier는 Desktop, Android 두 participant가 모두 도착하기 전에는 반환하지 않는다. 시간 지연 sleep으로 동시성을 흉내 내지 않는다.
- Android outage test는 controller를 통해 실제 sqld process를 멈추고 Android shared runtime 호출 결과를 수집한 뒤 동일 `database_path`로 다시 올린다. Private app root의 local Persona DB 및 message count를 read-only로 확인할 수 있다.

새 Desktop 파일은 `tests/` 아래에 있고 Android 추가 코드는 `src/debug/python` 및 `src/androidTest` 아래에 있다. Release production source에는 test control, observer 또는 fake provider 경로가 추가되지 않았다.

Harness prototype의 초기에 host control listener가 모든 interface에 bind되었다. 최종 acceptance 전에 `127.0.0.1`로 제한하고 emulator에는 두 test port만 `adb reverse`로 연결했다. All-interface 버전은 release artifact에 포함하지 않았으며 implementation commit에서 loopback-only 코드로 교체했다.

## 검증 결과

| 항목 | 결과 |
|---|---|
| Desktop focused harness primitive tests | 4/4 PASS |
| Android focused instrumentation | 3/3 PASS; failure/error/skip 0/0/0 |
| Desktop actual `recover_incomplete_turns` call | PASS; schema 24, synthetic Persona `7f6fa614-3df8-4a78-97a2-31e4a2f7c850` |
| Android actual recovery call | PASS; same Persona; Relationship stage durable completion 관찰 |
| Provider observer demo | PASS; invocation=1, `turn_id`와 origin device 기록, 외부 network 없음 |
| Stage observer | PASS; claim/execute/complete가 실제 Android stage recovery에서 모두 증가 |
| Desktop/Android barrier | PASS; 두 participant 동시 release |
| sqld start/stop/restart | PASS; 동일 testbed 경로로 restart 확인 |
| Android outage message/cognition 호출 | PASS; 호출 오류 결과와 DB 복구를 test에서 확인 |
| Durable result inspection | PASS; Android actual storage adapter로 turn/status/stage 확인 |
| Android unsigned Release rebuild | PASS; test hook 및 synthetic endpoint/token marker scan=0 |
| Android release APK | 21,485,786 bytes; SHA-256 `fd266643c9395c5487dba73fa73b214a8aff8436b4212e02067ab54c9e7eba7d` |
| Changed implementation secret scan | 5 files; confirmed real secret hits=0 |
| `git diff --check` | PASS |
| Relevant changed-area regressions | 0 |

개발 중 초기 세 Android harness iteration에서 test-stage plan mismatch, Python dict/JSON 형식 mismatch, 그리고 `core_completed`가 가능한 상태를 `complete`로 가정한 assertion 문제가 있었다. Test adapter/assertion만 수정했고, 최종 connected instrumentation run은 3/3, 0 failures/errors/skips다.

## 남은 제한

- Local `sqld`는 인증을 검증하지 않는다: `AUTHENTICATED_REMOTE_DB = NOT_PROVEN`.
- Synthetic provider observer는 harness callback 및 count/identity 관찰성을 보인다. 실제 provider replay safety는 다음 milestone에서 실제 runtime scenario로 측정해야 한다.
- Outage invocation은 호출 가능성/결과 관찰만 승인한다. No fallback, no Persona fork, outage cognition safety는 아직 capability로 승격하지 않았다.
- Full Desktop product regression 및 Desktop release build는 수행하지 않았다. 변경은 test-only이고 Desktop focused harness suite가 통과했다.

## 최종 마커

```text
M7.3.3-A.2.1.2.5 = PASS
TECHNICAL_ACCEPTANCE = PASS
CROSS_DEVICE_RECOVERY_HARNESS = TRUE
DESKTOP_RECOVERY_ENTRY_POINT = app.main lifespan → app.services.turn_recovery.recover_incomplete_turns
ANDROID_RECOVERY_ENTRY_POINT = runtime.core.CoreRuntime.bind_shared_storage → recover_foreground_turns + CoreCognitionRuntime.recover
DESKTOP_RECOVERY_CALLABLE = TRUE
ANDROID_RECOVERY_CALLABLE = TRUE
SCHEMA_VERSION = 24
PERSONA_ID = 7f6fa614-3df8-4a78-97a2-31e4a2f7c850
SAME_SHARED_PERSONA_P = TRUE
SYNTHETIC_PROVIDER = PASS
PROVIDER_INVOCATION_COUNTER_OBSERVABLE = TRUE
PROVIDER_TURN_ID_OBSERVABLE = TRUE
PROVIDER_ORIGIN_DEVICE_OBSERVABLE = TRUE
STAGE_CLAIM_OBSERVABLE = TRUE
STAGE_EXECUTE_OBSERVABLE = TRUE
STAGE_COMPLETE_OBSERVABLE = TRUE
MEMORY_MUTATION_OBSERVABLE = TRUE
RELATIONSHIP_MUTATION_OBSERVABLE = TRUE
ADDITIONAL_COGNITION_OBSERVABLE = TRUE (Episode inspection)
CROSS_DEVICE_BARRIER = PASS
DETERMINISTIC_RELEASE = YES
SLEEP_ONLY_RACE = NO
DB_START = PASS
DB_STOP = PASS
DB_RESTART_SAME_STORAGE = PASS
ANDROID_OUTAGE_WRITE_CALLABLE = TRUE
ANDROID_OUTAGE_COGNITION_CALLABLE = TRUE
OUTAGE_RESULT_OBSERVABLE = TRUE
LOCAL_FALLBACK_INSPECTABLE = TRUE
FOCUSED_TESTS = 7/7
FOCUSED_FAILURES = 0
FOCUSED_ERRORS = 0
FOCUSED_CORE_SKIPS = 0
NEW_REGRESSIONS = 0
CONFIRMED_REAL_SECRETS_ANDROID = 0
CONFIRMED_REAL_SECRETS_DESKTOP = 0
TEST_PROVIDER_HOOK_IN_RELEASE = NO
FAILURE_INJECTION_IN_RELEASE = NO
TEST_DB_ENDPOINT_IN_RELEASE = NO
CROSS_DEVICE_RECOVERY_HARNESS = TRUE
DESKTOP_RECOVERY_TEST_ENTRYPOINT = TRUE
ANDROID_RECOVERY_TEST_ENTRYPOINT = TRUE
SYNTHETIC_PROVIDER_OBSERVABILITY = TRUE
COGNITION_STAGE_OBSERVABILITY = TRUE
CROSS_DEVICE_RACE_HARNESS = TRUE
ANDROID_OUTAGE_TEST_HARNESS = TRUE
SHARED_RUNTIME_SAFETY = PARTIAL
CROSS_DEVICE_PROVIDER_OWNERSHIP = NOT_PROVEN
POST_COGNITION_EXACTLY_ONCE = NOT_PROVEN
FULL_COGNITION = TRUE
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
PROCEDURAL_WARNINGS = 3
CRITICAL_VIOLATIONS = 0
DESKTOP_IMPLEMENTATION_COMMIT = 30fdda16577ad27c8eb9b805c7665ea05545c96d
ANDROID_IMPLEMENTATION_COMMIT = 238859c802f507e714c83f74ee800123df691767
ANDROID_SUPPORTING_COMMIT = db882a823710f1e4ba640b3e22931a1cf589ac00
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
NEXT_DECISION = FINAL_SHARED_RUNTIME_SAFETY_EVIDENCE
```

Historical `M7.3.3-A.2.1.2.2 = PASS` and `M7.3.3-A.2.1.2.3 = FAIL` remain unchanged.
