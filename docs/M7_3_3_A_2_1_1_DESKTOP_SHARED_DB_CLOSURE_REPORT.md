# MindCore M7.3.3-A.2.1.1 — Desktop Shared DB Closure

**Final verdict: FAIL / NO-GO**

**Date:** 2026-09-30 (Asia/Seoul)

**Desktop base/final HEAD:** `43322b9030a2994a01fa81abd8978fcb9b2a8d9c` (`feature/m73-desktop-sync`)

**Android base/final HEAD:** `5de7a96d995d22e4f8735c6e85923dab06a8e9a5` (`main`)

**Shared Persona schema:** 24.

## Shared testbed result

Desktop participated in an actual local shared-DB run against the same official `sqld` v0.24.32 database used by Android. The test exercised the real Desktop `TursoPool`, repository, experience, memory, and relationship APIs with synthetic Persona `7f6fa614-3df8-4a78-97a2-31e4a2f7c850`. Android device `f789aed2-5761-44b4-a076-532a45bacf2c` and Desktop device `98e27b8c-071a-4bf2-8f29-8417178b80a7` wrote and read shared state in conversation `7159cc2e-4a6a-40d5-aa5a-e95be91546e8`.

Desktop read Android's completed chat/cognition writes; Android reattached and read Desktop-origin messages, Memory, and Relationship. A final product-adapter query found schema baseline 24, 19 message rows / 19 unique IDs, 8 Experience rows / 8 unique IDs, 2 Memory rows / 2 unique IDs. The target conversation had 11 messages from both devices with 11 unique IDs.

This is not production transport or authenticated credential acceptance. The local testbed had no JWT authentication and used HTTP. Desktop's production `create_pool` continues to reject HTTP; for this test, the harness directly instantiated the actual `TursoPool` against the exact local loopback endpoint. Therefore Keychain-to-database connection and production UI/provider behavior remain unproven.

## Acceptance and limitations

| Gate | Result |
| --- | --- |
| Actual Desktop `TursoPool` attach to shared schema-24 DB | PASS |
| Desktop product persistence/domain write path | PASS |
| Same authoritative Persona ID observed by Android and Desktop | PASS |
| Distinct Android/Desktop device IDs | PASS |
| Android writes visible from Desktop | PASS |
| Desktop messages, Memory, Relationship visible from Android after reattach | PASS |
| Duplicate durable message/Experience IDs in observed testbed | 0 |
| Production Desktop pool rejects HTTP | PASS; test policy regression passed |
| Desktop Keychain credential used for authenticated DB connection | NOT RUN; server was unauthenticated |
| Concurrent append/lost update/collision injection | NOT RUN |
| Outage writes, no-fallback, restore and reconnect behavior | NOT RUN |
| Cross-device provider ownership/replay and exactly-once cognition | NOT RUN |
| Actual Tauri UI action / live provider request | NOT RUN |

## Regression and Desktop builds

| Area | Result |
| --- | --- |
| Desktop Python | 637 passed, 1 skipped, 380 subtests passed |
| Frontend | 98/98 passed |
| Rust | 60/60 passed |
| Frontend production build | PASS; one non-fatal ineffective dynamic import warning |
| Fresh arm64 PyInstaller sidecar | PASS; 27,803,792 bytes |
| Fresh Tauri release executable | PASS via `tauri build --no-bundle`; 17,480,704 bytes |
| Sidecar test-tool/fixture marker scan | 0 matches; temporary pytest package removed before build |
| `git diff --check` | PASS |
| Changed-text strong-secret scan | 61 files; 0 private-key/token/JWT/provider-key pattern hits |

The final diff scan covered the accumulated Desktop M7.3.2/M7.3.3 candidate and reports. The historical A, A.1, A.2, and A.2.1 FAIL reports were not changed. No production Persona, production DB, production credential, or signing key was accessed. The only new Desktop closure artifact is this report. All existing dirty work is preserved.

Because authenticated DB access, concurrency, outage recovery, and replay/duplication gates remain unverified, **no commit was made**. No remote push, tag, or release occurred.

```text
MINDCORE_M7_3_3_A_2_1_1_DESKTOP = FAIL
SHARED_SCHEMA = 24
SHARED_PERSONA_ID = 7f6fa614-3df8-4a78-97a2-31e4a2f7c850
DESKTOP_DEVICE_ID = 98e27b8c-071a-4bf2-8f29-8417178b80a7
ANDROID_DEVICE_ID = f789aed2-5761-44b4-a076-532a45bacf2c
REAL_SHARED_TURSOPOOL_AND_DOMAIN_PATH = PASS
ANDROID_TO_DESKTOP_READBACK = PASS
DESKTOP_TO_ANDROID_READBACK = PASS
MESSAGE_ROWS_UNIQUE_IDS = 19/19
EXPERIENCE_ROWS_UNIQUE_IDS = 8/8
MEMORY_ROWS_UNIQUE_IDS = 2/2
AUTHENTICATED_KEYCHAIN_DB_CONNECT = NOT_PROVEN
CONCURRENT_APPEND_FAILURE_INJECTION = NOT_RUN
OUTAGE_NO_FALLBACK_RECONNECT = NOT_RUN
CROSS_DEVICE_PROVIDER_REPLAY = NOT_RUN
POST_COGNITION_EXACTLY_ONCE = NOT_RUN
DESKTOP_SIDECAR_BYTES = 27803792
DESKTOP_TAURI_BYTES = 17480704
DESKTOP_SECRET_SCAN_HITS = 0
DESKTOP_DIFF_CHECK = PASS
LOCAL_COMMIT = NOT MADE (milestone FAIL)
REMOTE_PUSH_TAG_RELEASE = NOT DONE
```
