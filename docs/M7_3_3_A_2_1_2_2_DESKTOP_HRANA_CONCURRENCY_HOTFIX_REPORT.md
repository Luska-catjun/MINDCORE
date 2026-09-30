# MindCore M7.3.3-A.2.1.2.2 — Desktop Hrana Concurrent Write Hotfix

**Verdict: PASS / GO**
**Technical acceptance: PASS**
**Date:** 2026-09-30 (Asia/Seoul)
**Desktop base:** `43322b9030a2994a01fa81abd8978fcb9b2a8d9c` (`feature/m73-desktop-sync`)
**Android base:** `5de7a96d995d22e4f8735c6e85923dab06a8e9a5` (`main`)
**Schema:** 24; schema 25 was not added.

## Summary

This hotfix addresses the Desktop repository path that failed under concurrent Hrana writes. The failure was reproduced before code changes, with a trace scoped to synthetic operation tags and the actual Desktop `TursoPool` plus `create_message` path. The corrected implementation serializes message writes per `TursoPool`, keeps each write's connection/stream operation-owned, and reconciles an ambiguous result by stable message ID on a fresh connection before considering a bounded retry.

The local schema-24 `sqld` testbed passed Desktop-only 10, 50, and 100 write bursts, then cross-device 10-pair and 50-pair appends through the actual Desktop repository and Android runtime. Both runtime paths observed the expected rows. Loss, duplicate rows, ID collisions, and unrecovered `STREAM_EXPIRED` errors were zero. The milestone does not close database-outage, provider-ownership, or post-cognition exactly-once safety.

## Original failure and root cause

| Probe | Result |
| --- | --- |
| One Desktop repository append concurrent with one Android runtime turn | Both completed; the failure required the larger burst. |
| Pre-fix Desktop-only 10-way actual repository write | 0/10 committed; 10/10 raised Hrana `STREAM_EXPIRED`; 0 tagged durable rows. |
| Pre-fix paired burst observation | Reproduced Desktop losses; an early Android fixture assertion also rejected concurrent outside rows because it required an exact `prior + 2` total. The fixture was corrected to identify the turn by its stable message contents/IDs. |
| Post-fix Desktop-only bursts | 10/10, 50/50, and 100/100 committed. |

The pre-fix per-operation trace recorded these ordered calls for each operation: `BEGIN` request succeeded → `INSERT` request succeeded → `COMMIT` attempted and returned `STREAM_EXPIRED` → rollback attempted on that expired transaction and also failed → the leased connection closed. The server logged expired stream handles. Example trace operation tags were `M7333A2122-TRACE-00` through `M7333A2122-TRACE-09`; credentials, auth headers, and baton values were not logged. The exact failure stage was **COMMIT**, after all ten operations had completed their insert request and before a durable row was visible.

The Desktop pool already created a separate native `libsql.Connection` for every `acquire()` and closed it at the end of that lease. No mutable Hrana stream/baton was shared between repository operations. Within an operation, awaits serialized `BEGIN`, insert, and commit; the Python driver exposes no raw baton getter, so only its opaque active/dead lifecycle was recorded. The fan-out left many write transactions open across separate HTTP Hrana requests. Earlier streams idled while the rest of the insert wave ran, and `sqld` expired them before their later commit request. The failed transaction's rollback used its dead stream; the pool closed that connection and did not retain it for another operation.

## Root-cause checklist

| Question | Finding |
| --- | --- |
| Stream shared between independent repository writes? | **NO** — one new raw connection/stream per pool lease. |
| Concurrent HTTP requests on one stream? | **NO** — a single repository operation awaited each request in sequence. |
| Expired baton reused by a later operation? | **NO** — each lease closed in `finally`; the failed lease was discarded. |
| Repository/client holds one stream for longer than an operation? | **NO** — no pool-lifetime connection; concurrent operations each held their own stream until completion. |
| Transaction lifetime too long under fan-out? | **YES** — the open transaction spanned multiple HTTP requests and waited through the concurrent insert wave before COMMIT. |
| Expiry state invalidated? | **YES** — failed rollback marks that connection unusable; lease close discards it. |
| Blind retry on ambiguous write? | **NO** — the old code did not retry `STREAM_EXPIRED`; the hotfix now reads the stable ID before any retry. |

**ROOT_CAUSE:** unbounded in-process `create_message` fan-out kept multiple mutable Hrana transactions open across `BEGIN`/`INSERT`/`COMMIT` requests. During a ten-write burst, earlier streams idled until the server expired their handles; the subsequent `COMMIT` calls failed. It was not cross-operation baton sharing or request concurrency on one stream.

## Fix

- **Stream ownership:** `TursoPool.message_write_scope()` adds a pool-local async lock around the complete message append operation. Each append still acquires a fresh connection, starts a short transaction, commits or rolls back, and releases/closes its own stream. Reads and non-Turso `asyncpg` pools are unchanged.
- **Request ordering:** The write scope allows at most one Desktop `create_message` transaction from a given `TursoPool` to be active at a time. Independent Android requests remained active in cross-device tests.
- **Expiry invalidation:** A failed operation never returns its `TursoConnection` to a reusable pool; `acquire()` closes it. Rollback cleanup marks an unusable transaction, and the next attempt opens a new connection.
- **Ambiguous commit handling:** One `message_id` is created before the retry loop and reused on every attempt. On `STREAM_EXPIRED`, the expired connection is released, then a fresh connection looks up that ID. If it exists, the call reconciles as success. If absent, a new transaction may retry with that same primary key, protected by the existing message PK and unique `(conversation_id, sequence)` constraint.
- **Retry policy:** Only exact `STREAM_EXPIRED` and the existing auto-sequence `database is locked` case are retryable. The total budget is three attempts (at most two retries), with 10 ms and 20 ms bounded backoff. Constraint, schema, validation, and Persona errors are not retried. A reconciliation read error aborts without retry. Logs expose attempt/reopen counts and error types only; no token or baton is emitted.

The Desktop-specific write lock is process-local to one `TursoPool`; this acceptance proves the product's single Desktop runtime against concurrent Android writes. It does not claim a distributed lock across several separate Desktop app processes.

## Focused acceptance

All required focus cases passed with zero focused failures, errors, or core skips:

```text
FOCUSED_TESTS = 21/21
FOCUSED_FAILURES = 0
FOCUSED_ERRORS = 0
FOCUSED_CORE_SKIPS = 0
```

| Gate | Result |
| --- | --- |
| Original expiry reproduction and exact failure stage | PASS — `COMMIT`; reproduced before fix. |
| Desktop single and sequential writes | PASS. |
| Desktop 10 concurrent repository calls | 10/10 durable; loss 0; duplicates 0. |
| Desktop 50 concurrent repository calls | 50/50 durable; loss 0; duplicates 0. |
| Desktop 100 concurrent repository calls | 100/100 durable; loss 0; duplicates 0. |
| Injected commit-applied/response-expired outcome | PASS — stable-ID fresh-read reconciliation returned the existing row without a second insert. |
| Injected expired-before-commit retry | PASS — retry reused the same ID and produced one durable row. |
| Retry bound and no infinite retry | PASS — three total attempts maximum. |
| Next operation after exhausted expired stream | PASS — fresh operation succeeded; no pool-wide poisoning. |
| Cross-device 10 pairs | PASS — 10 Desktop rows and 20 Android message rows visible through both runtimes; loss/duplicates/ID collisions 0. |
| Cross-device 50 pairs | PASS — 50 Desktop rows and 100 Android message rows visible through both runtimes; loss/duplicates/ID collisions 0. |
| Unrecovered `STREAM_EXPIRED` after fix | 0. |

Desktop runtime checks used `TursoPool`, `create_message`, and `list_messages`. Android ran its real shared storage runtime via the debug-only loopback fixture; the fixture queried rows through the bound runtime storage adapter. It was not a direct-sql-only substitute. Local endpoint policy remains confined to Android `src/debug`; Desktop production `create_pool` continues to reject HTTP.

The 10- and 50-pair tests share the existing synthetic Persona (`7f6fa614-3df8-4a78-97a2-31e4a2f7c850`) and schema-24 testbed. Desktop and Android device IDs stayed distinct (`98e27b8c-071a-4bf2-8f29-8417178b80a7` and `f789aed2-5761-44b4-a076-532a45bacf2c`). The local unauthenticated testbed does not prove authenticated remote DB access.

## Regression and builds

| Area | Result |
| --- | --- |
| Android Python | 102/102 PASS |
| Android JVM | 9/9 PASS; `testDebugUnitTest` PASS |
| Android instrumentation | 70 passed, 33 phase/acceptance-gated skips, 0 failures |
| Desktop Python | 642 passed, 1 skipped, 380 subtests passed |
| Desktop frontend | 98/98 PASS |
| Desktop Rust | 60/60 PASS |
| New regressions | 0 |
| Focused repository tests after final logging change | 10/10 PASS |
| Android fresh Debug APK | PASS — 21,564,524 bytes; SHA-256 `0213e92dc41d9d69f33e11e6af4e5d0ab505a3a14eb6bc8ecb4b1320daa1468e` |
| Android fresh unsigned Release APK | PASS — 21,485,786 bytes; SHA-256 `0f7a1e6237706d7799637058454700b869c2217a6ec6367a2e973d385a3f357c` |
| Desktop frontend production build | PASS |
| Desktop arm64 sidecar | PASS — 27,805,344 bytes |
| Desktop Tauri release executable (`--no-bundle`) | PASS — 17,480,704 bytes |

The first general Android instrumentation invocation also included the shared-DB scenario without its explicit acceptance flag. Two old-data fixture assertions failed because the bounded Android chat-history view no longer contained the required seed marker. The shared-DB and concurrency fixture tests are now explicitly opt-in, and the ordinary full instrumentation rerun passed. Focused 10/50-pair acceptance continued to pass with its explicit flag. No production runtime behavior was changed to accommodate the fixture.

## Security, diff, and gates

- Changed-scope strong-pattern scans: Android 36 text paths, Desktop 71 text paths; **0 confirmed secret hits**.
- Production security weakened: **NO**. No plaintext credential fallback, production HTTP bypass, trust-all configuration, provider key logging, or schema change was introduced.
- `git diff --check`: PASS in both repositories.
- `FULL_COGNITION = TRUE` remains the previously established M6.4 capability.
- `AUTHENTICATED_REMOTE_DB = NOT_PROVEN`; the synthetic local server had no auth token and used HTTP.
- Historical M7.3.3-A through A.2.1.2.1 FAIL reports were preserved unchanged.
- No forbidden production Persona/DB/credentials, updater secrets, signing keys, or unrelated repository were accessed.
- Procedural warnings: **2** — the first wrapper invocation used the repository root instead of its `android/` subdirectory, and its retry omitted the bundled JDK environment. Both were corrected without changing files or acceptance evidence.

```text
M7_3_3_A_2_1_2_2 = PASS
TECHNICAL_ACCEPTANCE = PASS
PROCEDURAL_WARNINGS = 2 (initial wrapper lookup from repository root; initial Gradle invocation lacked the bundled JDK environment, both corrected)
ROOT_CAUSE_IDENTIFIED = YES
DESKTOP_SINGLE_WRITE = PASS
DESKTOP_SEQUENTIAL_WRITES = PASS
DESKTOP_CONCURRENT_10_50_100 = PASS
DESKTOP_HRANA_CONCURRENT_WRITES = TRUE
CONCURRENT_SHARED_WRITES = TRUE
STREAM_EXPIRED_UNRECOVERED = 0
EXPIRED_BATON_REUSED = 0
AMBIGUOUS_COMMIT_SAFE = PASS
DUPLICATE_SAFE_RETRY = PASS
RETRY_BOUNDED = YES
DESKTOP_TO_ANDROID_SMOKE = PASS
ANDROID_TO_DESKTOP_SMOKE = PASS
CROSS_DEVICE_10_50_PAIRS = PASS
CONCURRENT_APPEND_LOSS = 0
DUPLICATE_DURABLE_ROWS = 0
STABLE_ID_COLLISIONS = 0
NEW_REGRESSIONS = 0
SHARED_PERSONA_DB = TRUE
PERSONA_SHARED_RUNTIME = TRUE
SAME_PERSONA_IDENTITY = TRUE
STREAM_EXPIRY_RECOVERY = TRUE
AMBIGUOUS_COMMIT_IDEMPOTENCE = TRUE
DEVICE_PERSONA_REPLICA_REQUIRED = FALSE
MANUAL_SYNC_FILE_REQUIRED = FALSE
FULL_COGNITION = TRUE
SHARED_RUNTIME_SAFETY = PARTIAL
AUTHENTICATED_REMOTE_DB = NOT_PROVEN
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
NEXT_DECISION = M7.3.3-A.2.1.2.3 FINAL SHARED RUNTIME SAFETY CLOSURE
```

## Commit disposition

Technical acceptance is PASS. The Desktop implementation/test commit is `ce5effc` (`Fix Hrana concurrent write stream lifecycle`); the Android acceptance-fixture commit is `c9e3a9a` (`Add shared DB concurrency acceptance harness`). This report is a separate docs-only local commit; the final branch tips are recorded in the task closeout. No remote push, tag, or release was made.
