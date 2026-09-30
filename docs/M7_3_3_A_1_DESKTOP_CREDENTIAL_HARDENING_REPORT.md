# MindCore M7.3.3-A.1 — Desktop Credential Hardening

**Final verdict: FAIL / NO-GO**  
**Date:** 2026-09-30 (Asia/Seoul)  
**Schema:** 24; no schema 25.  
**HEAD:** `43322b9030a2994a01fa81abd8978fcb9b2a8d9c`  
**Branch:** `feature/m73-desktop-sync`

See the Android report [M7.3.3-A.1 shared DB E2E report](../../mindcore-android/docs/M7_3_3_A_1_SHARED_DB_E2E_REPORT.md) for the shared-runtime acceptance checklist and Android build/test evidence.

## Decision summary

Desktop DB token persistence was changed to use the existing fail-closed `NativeKeyringBackend` through a fixed sidecar credential broker. Persona profile files now serialize a `DATABASE_CREDENTIAL_ID` reference instead of `DATABASE_AUTH_TOKEN`; the native host resolves the secret only for a child runtime/setup process. Credential references are validated, missing entries fail closed, and no file-backed token fallback remains in the new runtime lookup path.

The feature is not accepted. The in-memory backend tests do not validate the native macOS keychain, no live native credential lifecycle or product startup was exercised, and no test-only shared DB endpoint was available for Desktop + Android. M7.3.3-A.1 exit criteria require both. The root-only tool-workdir rule was also violated by two frontend-subdirectory workdirs. No commit was made.

## Credential design and lifecycle

- OS service: `MindCore Persona Database`.
- Account: validated opaque credential reference, normally generated UUID; it is distinct from provider key accounts.
- `store`, `get`, and `delete` are exposed only as fixed sidecar CLI operations. Values do not become CLI arguments or config-file values.
- Rust `store` passes the token to the child via an environment entry. `get` captures stdout in memory and injects the result into only the runtime/setup child process. Error messages are fixed codes and do not include the credential value.
- New credential edits use a new opaque reference; the active profile continues pointing at its existing account until profile persistence succeeds. Replacement and deletion behavior is covered using an injected fake keyring backend.
- Missing credentials are errors; no config-file or provider-store fallback is attempted.
- Legacy profile migration code moves an existing profile token into the OS keyring and rewrites the profile without the token. It was not invoked against a user profile during this task.

| Requirement | Result |
| --- | --- |
| Persona config has DB URL plus credential reference | Implemented; Rust serialization regression covers it |
| Persona config serializes DB token | No; serializer omits the token and regression asserts absence |
| Store / lookup / replace / delete | 2/2 focused broker tests PASS with fake keyring backend |
| Missing credential and malformed reference | Safe fail-closed/reject behavior PASS in focused broker tests |
| Native macOS Keychain integration | Not exercised |
| Existing production profile migration | Not run; production config and credentials were not accessed |
| Provider credential handling | Existing provider key flow remains separate |
| Actual shared DB product E2E | Not run; endpoint unavailable |

`DATABASE_AUTH_TOKEN` remains an environment variable at the runtime API boundary by design, and legacy migration/tests still refer to its key name. Those source references do not embed a real credential. Newly written Persona profiles omit the key/value; no user configuration was scanned because that would cross the task boundary.

## Validation results

| Area | Result |
| --- | --- |
| Focused credential tests | 2/2 PASS |
| Desktop Python full suite | 636 passed, 1 skipped; 380 subtests passed |
| Frontend | 98/98 PASS |
| Frontend production build | PASS; one non-fatal ineffective dynamic import warning |
| Rust unit suite | 60/60 PASS |
| Rust release executable build | PASS; current size recorded in paired report |
| PyInstaller sidecar build | PASS after removal of temporary pytest package from `.venv` |
| Packaged Tauri app build | Not run in this turn |
| `git diff --check` | PASS |
| `cargo fmt --check` | FAIL against broad pre-existing unformatted dirty Rust sources; no bulk formatting changes were made |
| Changed-file secret scan | 44 paths; private-key/AWS/OpenAI/GitHub-token patterns: 0 hits; confirmed secret candidates: 0; placeholder hits: 0 |
| Persona config and native keychain scan | Not performed; user production config/keychain are outside scope |

## Procedure / repository state

- Baseline: `43322b9030a2994a01fa81abd8978fcb9b2a8d9c`, `feature/m73-desktop-sync`.
- The repository already contained dirty M7.3.2/M7.3.3-A candidate implementation and reports; these were preserved. No reset, clean, stash, rebase, amend, or checkout occurred.
- First filesystem action was the required Android-root `pwd` check. No `git commit`, push, tag, or release operation was performed.
- `FIRST_FILESYSTEM_ACTION_CORRECT=YES`; `TOOL_READ_BEFORE_FIRST_ACTION=NO`.
- `WORKDIR_OMITTED_CALLS=0`; `SUBDIRECTORY_WORKDIR_CALLS=2`; `PARENT_TRAVERSAL_CALLS=0`.
- `COMMAND_INTERNAL_CHDIR_CALLS=3`: one required initial `cd` and two `npm --prefix frontend` commands that ran scripts from the prefix directory. This is a procedure failure. No unrelated repo, production Persona DB, production credential, updater secret, or signing key was accessed.
- PyInstaller used its default build cache under `/Users/luska/Library/Application Support/pyinstaller`; no user data there was inspected.
- The earlier M7.3.3-A historical FAIL report was not changed.

## Exit criteria

| Gate | Result |
| --- | --- |
| Native OS-keyring lifecycle proven with isolated test account | FAIL / not run |
| No plaintext DB token in new config serialization | PASS |
| No fallback when keyring entry is missing | PASS by implementation and unit behavior |
| Actual Desktop and Android share the same synthetic schema-24 DB | FAIL / endpoint unavailable |
| Shared Persona ID and domain state proven in both products | FAIL / not run |
| Concurrent writes, restarts, offline no-fork, provider replay, post-cognition replay | UNMEASURED |
| Regressions, frontend/native builds, diff and secret checks | Passed as listed, with Android packaging/testing details in paired report |
| Repository-root-only workflow | FAIL / two subdirectory workdirs |
| Schema and remote-operation constraints | PASS / schema 24, no push/tag/release |

**FAIL / NO-GO. No local commit.** Keep this working tree dirty until a test-only shared endpoint and isolated native-keyring validation environment are available and the workflow is rerun without subdirectory workdirs.
