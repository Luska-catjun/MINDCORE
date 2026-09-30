# MindCore Desktop M7.3.2 — Sync counterpart final report

**최종 판정: FAIL / NO-GO.** 실제 AndroidKeyStore와 Desktop Keychain을
사용한 암호화 동기화 및 충돌 해결은 통과했습니다. 다만 두 제품 UI에서
pairing·파일 선택·충돌 해결을 끝까지 수행한 검증은 없습니다. Desktop의
현재 동기화 경로는 선택된 로컬 schema-24 `file:` DB에 한정됩니다.
`PERSONA_SYNC = FALSE`를 유지하고 수용 commit은 하지 않습니다.

**M7.3.2 = FAIL / NO-GO; `PERSONA_SYNC = FALSE`.** The Desktop conflict
engine, packaged sidecar, Tauri command, native file dialog UI, and two-Device
secure exchange are implemented. The required real production-screen journey
has not been exercised on both platforms. A Desktop resolution-specific actual
process-death retry passed, but it does not close the UI gap. The implementation and this report
remain uncommitted under the milestone's PASS-only commit rule.

## Baseline and scope

Desktop baseline `43322b9030a2994a01fa81abd8978fcb9b2a8d9c` on
`feature/m73-desktop-sync` was clean; Android baseline is
`5de7a96d995d22e4f8735c6e85923dab06a8e9a5`. The required first
filesystem action ran from Android. Tool workdirs were the exact two repo
roots, with no subdirectory workdir, internal chdir after that first action,
`..` path, forbidden project, production Persona/database/credential, source
authority, or M4.5 access. Historical M7.3.1 FAIL and M7.3.1.1 PASS remain.

## Desktop implementation

- `app/services/persona_sync.py` implements matching protocol-v1 encrypted
  resolution operations for mutable conflicts, delete/update races, and
  competing offline decisions. It validates heads, result hash, Persona,
  stale ancestry, and replay; it does not choose by timestamp. Sidecar schema
  5 retains only metadata, authenticated pending ciphertext, and a retained
  physical row hash behind a semantic tombstone. Persona schema stays 24.
- `scripts/mindcore_sync.py` exposes identity, peers, pair inspect/import/
  export, revoke, secure export/apply, conflict list/preview, and explicit
  resolve commands. Opening the Sync panel invokes the conflict command to
  establish a local baseline before later offline edits.
- The production React `SyncPanel` invokes Tauri `manual_sync_action`; the
  command verifies the active Persona and a selected local `file:` schema-24
  DB, validates dialog paths, and calls the packaged Python sidecar with an
  app-private state path. Native Tauri file dialogs supply pair/sync files.
  The current product bridge does not apply a live `libsql:`/Turso Persona.
  Peer names and last manual import status are local, Persona-scoped metadata.
- The UI includes full fingerprint confirmation, trusted/revoked peers,
  explicit revoke and delete-on-both confirmation, conflict previews,
  integrity repair notice, status, and safe errors. React tests validate
  dispatch and confirmation logic; they mock the Tauri invoke boundary.

## Cross-platform evidence and limitation

The isolated M7.3.2 acceptance harness used AndroidKeyStore, Desktop native
keyring, actual pairing artifacts, authenticated `.mindcoresync` files, the
Android test-only bridge, and the final packaged Desktop sidecar action. It
passed two offline append records with no duplicates, Android- and
Desktop-origin mutable resolutions, both tombstone choices in opposite
directions, restart, P1/P2 isolation and wrong-route rejection, zero pending
normal conflicts, hash equality, and a final zero-mutation round. This
demonstrates real crypto and backend interoperability, but
does not show a person driving both production UI screens and file dialogs.

The full product UI E2E criteria (pairing, file export/import, inbox decisions,
resolution, restart, final no-op) therefore remain **FAIL/unverified**. The
Android instrumentation matrix for all these user actions is incomplete.
The Desktop live Turso/local-file profile boundary also requires a product
decision or implementation before broad Persona Sync approval. A child process
was terminated immediately after committing a selected resolution and before
the sidecar checkpoint. A fresh process retried successfully, leaving one
domain mutation and one resolution row.

## Test and artifact record

| Check | Result |
|---|---:|
| Desktop Python suite | 621 pass / 622 run, 1 skip |
| Focused sync/security | 42/42 PASS |
| React frontend | 97/97 PASS |
| Rust | 59/59 PASS |
| Frontend production build | PASS |
| Tauri development build | PASS |
| PyInstaller arm64 sidecar and archive dependency verification | PASS |
| Sidecar archive fixture-name scan | 0 / 994 members |
| Android Python / JVM / instrumentation | 96/96; PASS; 35 pass / 30 expected skip / 0 fail |
| Android Debug / unsigned Release | PASS / PASS |
| Scoped changed-text secret scan | Desktop 0/28, Android 0/13 matches/files |
| `git diff --check` | PASS in both repositories |

The report at the Android counterpart contains the detailed marker and APK
sizes. No remote push, tag, release, or local acceptance commit was made.
`AUTO_SYNC`, `BACKGROUND_SYNC`, `CLOUD_SYNC`, and `AUTONOMY` remain FALSE;
existing accepted cognition and UI capabilities remain TRUE. The next step is
**SYNC_PRODUCT_HOTFIX**, not M7.4 autonomy.
