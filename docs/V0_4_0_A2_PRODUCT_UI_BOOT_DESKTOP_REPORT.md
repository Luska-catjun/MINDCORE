# V0.4.0-A2 Desktop Product UI / Persona Connection / Boot Report

## Result

**A2 desktop implementation: PARTIAL.** Product UI, boot-stage wiring, tests, sidecar, `.app`, and DMG are present. The full Tauri command exits non-zero after bundling because `TAURI_SIGNING_PRIVATE_KEY` is unavailable for the configured updater signature. Android instrumentation is also outstanding in the paired A2 report, so the milestone is not accepted and no implementation commit was created.

## Repository

- Start/final HEAD: `8380564303c06f3cee6c51f894e10be4c42ee3a3`
- Branch: `backup/mindcore-desktop-current-2026-09-30`
- Pre-existing untracked `.toolchain/` was preserved.
- `git diff --check`: PASS.
- No commit, push, tag, or release.

## Changes

- Replaced the simulated startup percentage and interval with stage-based boot UI. Tauri emits real native config, cognition, storage, binding, hydration, and ready stages; the sidecar forwarder accepts only fixed safe timing labels. The FastAPI binding preflight emits bounded start/end timing labels.
- Added Persona Connection, separating database health, Persona binding, and provider credential presence. The UI reads only provider name/configured metadata and never renders credential values.
- Kept the app navigable on server-side database failures by presenting `DB_UNAVAILABLE`; protected sends are disabled for mismatch, unavailable binding, or failed database health. `UNBOUND` remains eligible for the existing automatic bind contract.
- Changed shared CSS tokens and boot/connection visuals to deep navy, blue, cyan, and teal. Green is used only for success semantics.
- Removed the Sync / Devices entry and route from the product navigation. Historical `SyncPanel` and native sync commands remain in the source tree but are not reachable from product navigation.

## Verification

- Python: `654 passed, 3 skipped, 380 subtests passed` (full suite; one existing dependency deprecation warning).
- Frontend: `105 passed` across 18 files; production Vite build PASS.
- Rust: `60 passed`; `cargo check --locked` PASS.
- Sidecar: PyInstaller build PASS (Python 3.12.14, arm64).
- Tauri generated `MindCore.app` and `MindCore_0.3.0_aarch64.dmg`; DMG checksum verification PASS. The app executable is arm64 Mach-O.
- `npm run tauri:build` exit status: **FAIL after successful bundle generation**, because updater signing could not find `TAURI_SIGNING_PRIVATE_KEY`. No secret value was read or printed. Signed updater output was not produced.
- Deterministic UI evidence: Persona Connection tests exercise `BOUND_MATCH`, `UNBOUND`, `BOUND_MISMATCH`, and `DB_UNAVAILABLE`; sidebar test confirms the connection entry and absence of Sync / Devices; chat test confirms mismatch send is disabled. No live app screenshots were captured.

## Acceptance notes

The current A2 test run did not exercise a live native boot or capture the desktop screens. Desktop UI/build acceptance is supported by deterministic component tests and generated artifacts; signed updater packaging remains blocked on the named environment variable. The prior server-side PersonaBindingGuard and turn pipeline were not changed.

`LEGACY_BACKEND_CODE = PRESENT_BUT_NOT_PRODUCT_EXPOSED`

`REMOTE_PUSH = NO` · `REMOTE_TAG = NO` · `REMOTE_RELEASE = NO`
