# MindCore M7.3.3-A.2.1 — Native Keychain Closure Report

**Verdict: FAIL / NO-GO**  
**Date:** 2026-09-30 (Asia/Seoul)  
**Desktop base/final HEAD:** `43322b9030a2994a01fa81abd8978fcb9b2a8d9c`  
**Branch:** `feature/m73-desktop-sync`  
**Schema contract:** 24. No schema 25 was introduced.

## Native macOS Keychain evidence

The production-equivalent credential broker (`app.desktop_backend --database-credential-action`) was exercised with `NativeKeyringBackend` backed by the native macOS keyring module `keyring.backends.macOS`. A new synthetic random credential was stored under service `MindCore Persona Database`, fetched in a separate broker process without reinjecting the value, and compared in memory. The value was not printed, logged, or written to a file. A separate absent UUID returned `DATABASE_CREDENTIAL_MISSING`. The exact synthetic item was deleted in `finally` and cleanup succeeded.

- Synthetic credential ID: `ae5cad6d-11d6-4a52-b20a-042cc2e0b3ae`
- Native backend: `keyring.backends.macOS`
- Store: PASS
- New-process lookup after broker restart: PASS
- Missing item: safe failure PASS
- Exact test item cleanup: PASS
- Wrong credential against an authenticated DB: NOT RUN (no test DB endpoint)
- DB connection from the retrieved Keychain value: NOT RUN (no test DB endpoint)
- Desktop product/sidecar restart and DB reconnect: NOT RUN

No production Persona config, production DB credential, provider credential, or updater secret was read or changed. The static Rust serialization test and existing broker tests assert that newly rendered Persona configuration carries `DATABASE_CREDENTIAL_ID` and has no `DATABASE_AUTH_TOKEN` value. This is code/test evidence only; no actual user Persona profile was inspected. No plaintext fallback was exercised or found in the new broker lookup path.

## Shared DB closure decision

A checksum-verified official `sqld` v0.24.32 macOS arm64 binary was downloaded to the ignored Android toolchain cache and its CLI inspected. It was **not started**. The local server exposes HTTP; this release has no HTTP TLS listener option. The Desktop and Android shared adapters require HTTPS and validate the server certificate. No trusted HTTPS Hrana testbed was brought up, so starting an unauthenticated HTTP daemon would not satisfy this acceptance gate.

Therefore this run does not prove that the Desktop application runtime reads Keychain, connects to DB P, or shares state with Android. No synthetic test DB, test Persona, or cross-device state was created. No live provider was called. The native Keychain lifecycle pass does not close the shared-DB gate.

## Revalidation

| Area | Result |
| --- | --- |
| Desktop Python | 636 passed, 1 skipped, 380 subtests passed |
| Frontend | 98 passed / 98 |
| Rust | 60 passed / 60 |
| Frontend production build | PASS (`npm --prefix frontend run build`); one non-fatal ineffective dynamic import warning |
| Sidecar/PyInstaller | PASS; fresh arm64 binary `27,804,016` bytes; SHA-256 `075a8fc9d8459fa9575ebb9f8126171d7` |
| Sidecar test-tool exclusion | PASS; pytest was removed from the venv before the final sidecar build; binary strings scan found 0 pytest/A.2.1 credential markers |
| Tauri app build | PASS; fresh release executable built with `tauri build --no-bundle`; `17,480,704` bytes. Bundling/installers were not produced. |
| Native Keychain lifecycle | PASS as detailed above |
| Desktop shared DB runtime attach | NOT RUN |
| `git diff --check` | PASS |
| Changed text/path secret scan | 45 changed paths including this report; pattern hits 0; confirmed real secrets 0; placeholder hits 0 |
| Artifact scan | Sidecar test-only / credential markers: 0 |

The local Python venv lacked pytest at task start. Pytest was installed temporarily to execute the required full regression suite, then uninstalled before the fresh sidecar build. The final venv package list has no pytest. No dependency manifest was changed for this temporary test runner.

## Procedure and repository state

- The required first action in the paired run was exactly `cd /Users/luska/mindcore-android && pwd`; output matched the required path. No file/tool read occurred before it.
- All subsequent tool workdirs were explicitly one of the two repository roots. No frontend/subdirectory workdir, omitted workdir, explicit later `cd`, parent traversal, forbidden path, Diana path, production Persona, or production credential was accessed.
- The only shell `cd` was the mandated bootstrap command; command-internal chdir count excluding that mandated action: 0.
- Existing dirty M7.3.2/M7.3.3-A/A.1 work and historical FAIL reports were preserved. Current Desktop HEAD remains the baseline. No source implementation was rewritten for A.2.1.
- No commit was made because the milestone is FAIL. No push, tag, or release was made.

## Final result

**M7.3.3-A.2.1 = FAIL / NO-GO.** Native macOS Keychain storage and post-process lookup are now directly verified, but the key was not used by an actual Desktop DB runtime. A trusted local HTTPS Hrana endpoint and the resulting Desktop + Android same-database workflow remain required before closure.
