# MindCore Desktop M7.3.1.1 — Secure Sync Closure

**Result: PASS.** This report records the Desktop half of the actual paired
Android/Desktop acceptance. The earlier M7.3.1 report remains **FAIL / NO-GO**
and is preserved without edits.

## Baseline and procedure

- Branch: `feature/m73-desktop-sync`; source base:
  `072af1640efbbc44bf36544a41d77a436fc08883`.
- Android acceptance base:
  `11707e007e2c1fcd4c2c1c31c1167422fdad6e2a`; schema 24 on both platforms.
- Only `/Users/luska/mindcore-android` and
  `/Users/luska/mindcore-desktop-sync` were used as tool workdirs. No
  subdirectory/read-only workdir, internal chdir, or parent traversal was used
  in this closure run. The required first filesystem action was completed by
  the earlier M7.3.1.1 continuation before this closure work.
- Expected M7.3.1 candidate changes were preserved; no unrelated dirty changes
  were found. Desktop source authority and M4.5 exporter were not modified.
  Production Persona/database, provider/Turso/updater credentials, permanent
  signing secrets, and `/Users/luska/diana` were not accessed.

## Actual interoperability results

- Compared canonical synthetic record JSON and SHA-256; all 28 sync policy
  tuples matched. Real delta exchanges converged to semantically equal
  manifests.
- AndroidKeyStore and Desktop native-keyring identities exchanged real public
  pairing artifacts with confirmed matching fingerprints. Trust persisted over
  Android process restart. Revoke was exercised both ways; each revoked peer's
  actual envelope was rejected as `REVOKED_PEER`, with no mutation.
- The Desktop CLI applied Android-encrypted envelopes. Android's secure inbound
  path applied Desktop-encrypted envelopes. Both same-envelope replay and
  wrong sender, Persona, recipient, ciphertext tampering, and revoked peer were
  rejected. Envelope payload stayed encrypted; import did not call a provider.
- Three rounds of independent offline append converged to six unique records
  with zero loss or duplicates. The next round had empty deltas. Uncontested
  mutable changes converged in both directions.
- Concurrent mutable edits and a real delete/update race created durable
  conflicts on both peers with matching `TOMBSTONE_CONFLICT` classification.
  Neither side applied LWW or automatic conflict resolution.
- Explicit tombstones traveled in both directions, were idempotent, and did
  not delete remote Persona rows or independent cognition state. P1/P2
  synchronization and metadata remained isolated; wrong-Persona routing was
  rejected.
- Desktop's commit-before-checkpoint crash/retry remains covered by the local
  failure-injection test. Android remote-apply recovery reuses the accepted
  M7.2.1.1 regression and connected suite.

## Narrow acceptance correction

The paired delete/update test exposed a missing case in Desktop apply: when
its local message row had already been removed and its sync sidecar retained a
tombstone, a remote update was reported as `REMOTE_ANCESTRY_UNKNOWN`. The
apply path now consults the existing local tombstone metadata before treating
the record as a new entity. It reports `CONCURRENT_DELETE_MUTATION` and
durably stores `TOMBSTONE_CONFLICT`, matching Android. The new focused
regression test passes, and the actual secure exchange confirms both local
values remain unchanged after conflict detection.

## Regression and artifacts

| Area | Result |
|---|---:|
| Desktop Python suite | **616 passed, 1 skipped** |
| Focused crypto, sync, and sidecar tests | **43/43 PASS** |
| Frontend | **91/91 PASS** |
| Rust | **59/59 PASS** |
| Frontend production build | **PASS** |
| Tauri development build | **PASS** |
| PyInstaller arm64 sidecar build | **PASS** |
| Sidecar dependency verification | **PASS** |
| Rebuilt sidecar test-fixture scan | **0 test fixture members** |
| Scoped secret scan | **13 changed text files, 0 matches** |
| `git diff --check` | **PASS** |

The sidecar keeps private sync identity in the OS native keyring. Trust,
replay, checkpoints, and conflicts are kept in the per-device sidecar; no
Persona payload or provider credential is written there. No remote push, tag,
or release was performed.

## Capability decision

```text
MINDCORE_M7_3_1_1 = PASS
DESKTOP_SYNC_COUNTERPART = TRUE
MANUAL_SECURE_TRANSPORT = TRUE
BIDIRECTIONAL_CONVERGENCE_FOUNDATION = TRUE
SYNC_FOUNDATION = TRUE
SECURE_SYNC_CORE = TRUE
REMOTE_APPLY_FOUNDATION = TRUE
PERSONA_SYNC = FALSE
FULL_COGNITION = TRUE
UI_PRODUCTION_READY = TRUE
AUTONOMY = FALSE
ANDROID_SCHEMA_VERSION = 24
DESKTOP_SCHEMA_VERSION = 24
SCHEMA_FORK_CREATED = NO
DESKTOP_ENCRYPT_ANDROID_DECRYPT = PASS
ANDROID_ENCRYPT_DESKTOP_DECRYPT = PASS
PAIRING_TRUST_RESTART_PERSISTENCE = PASS
REVOKED_PEER_REJECTED_BOTH_DIRECTIONS = YES
OFFLINE_APPEND_ROUNDS = 3
SYNC_RECORD_LOSS = 0
SYNC_DUPLICATE_RECORDS = 0
CONCURRENT_MUTATION_CONFLICT = PASS
DELETE_UPDATE_CONFLICT = PASS
DESKTOP_TOMBSTONE_TO_ANDROID = PASS
ANDROID_TOMBSTONE_TO_DESKTOP = PASS
MULTI_PERSONA_E2E_ISOLATION = PASS
DESKTOP_REMOTE_APPLY_CRASH_SAFE = PASS
PLAINTEXT_PERSONA_TRANSPORT = NO
DESKTOP_PRIVATE_SYNC_KEY_PLAINTEXT = NO
CENTRAL_SERVER_IMPLEMENTED = NO
HISTORICAL_M7_3_1_FAIL_PRESERVED = YES
DESKTOP_COMMIT_SHA = RECORDED_AFTER_LOCAL_COMMIT
ANDROID_COMMIT_SHA = RECORDED_AFTER_LOCAL_COMMIT
REMOTE_PUSH = NO
REMOTE_TAG = NO
REMOTE_RELEASE = NO
NEXT_DECISION = M7_3_2_CONFLICT_AND_SYNC_UX
```

`PERSONA_SYNC` stays false until M7.3.2 completes conflict resolution,
trusted-device and manual transport UI, status visibility, and final user
acceptance.
