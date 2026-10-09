# MindCore 0.4.0 / Android 0.1.0 Public Release Report

Date: 2026-10-09 (Asia/Seoul)

## Final decision

```text
PUBLIC_RELEASE_STATUS = COMPLETE
DESKTOP_PUBLIC_RELEASE = PASS
ANDROID_PUBLIC_RELEASE = PASS
MINDCORE_DESKTOP_0_4_0 = RELEASED
MINDCORE_ANDROID_0_1_0 = RELEASED
ANDROID_PUBLIC_UPDATE_E2E = PARTIAL
```

COMPLETE means both public distributions and their updater delivery metadata passed verification. The complete Android UI download-to-installer path was not acceptance-proven: the older code 2 app ran out of heap during download progress rendering. A separate real PackageManager upgrade from code 2 to the public code 3 APK passed, including synthetic data preservation. This limitation is not classified as a proven regression in the current code 3 app.

## Source

```text
FINAL_RELEASE_COMMIT = dc2b5f634ff86702978719581966f53b7dde5054
TAG = v0.4.0
MAIN_HEAD = dc2b5f634ff86702978719581966f53b7dde5054
VERIFICATION_HEAD = dc2b5f634ff86702978719581966f53b7dde5054
PROMOTION = NORMAL_FAST_FORWARD
DESKTOP_RUNTIME_SINCE_c374366 = UNCHANGED
DESKTOP_RELEASE_WORKFLOW = UNCHANGED
VERIFICATION_EVIDENCE_PRESERVED = YES
ANDROID_ACCEPTED_SOURCE_HEAD = 006309efa2d5e1ff4cd188ed96ab0884731cc2bb
ANDROID_URL_MIGRATION_SOURCE = 978ee1d022a6ffd41822fd3de67b15b6e68eb1a1
ANDROID_URL_MIGRATION_BRANCH = codex/android-public-repo-migration
FEEDBACK_SOURCE_HEAD = dd60dae3c8fcd7bdc16b062da3235813340b7d91
```

- Public Desktop repository: `Luska-catjun/MINDCORE`. The local `origin` remains the private backup; publication used the public repository explicitly.
- Previous public main and merge base: `072af1640efbbc44bf36544a41d77a436fc08883`. Divergence before promotion: 0 main-only / 36 verification-only commits.
- Main was promoted normally, then the absent annotated tag `v0.4.0` was created and pushed. Its tag object is `8c5ddf714927b9a5051f0c4133694faf5f98eaeb`; it resolves to FINAL_RELEASE_COMMIT.
- The only Desktop differences since runtime fix `c37436657ec69fa671775e126119707b78834659` at publication were the two existing readiness/audit documents. This new report is a subsequent docs-only commit on `codex/v0.4.0-public-release-report`; public main and the release tag remain at the verified source.
- All three repositories started clean. Android's original readiness branch is preserved. No assertions, skips, dependencies, schemas, signing keys, updater implementation, or Desktop product files were changed during deployment.

### User-authorized Android repository migration

After initial publication to the source-defined MINDCORE Android channel, the user supplied `https://github.com/Luska-catjun/MINDCORE_Android.ver` and explicitly selected **“업데이트 URL도 새 저장소로 이전하도록 freeze 해제”**.

The resulting product-source change is exactly two URL values in `android/version.properties`: `updateManifestUrl` and `apkUrlTemplate`. VersionName 0.1.0 and versionCode 3 remain unchanged. The same permanent signing key was used. The public distribution repository was initially empty and was initialized with distribution README metadata only, at `e899694b6a22ac295c17cc866f0ddc0f990bb811`. No private product source/history was copied into it. Its delivery tags identify distribution metadata; Android artifact source provenance is the migration source commit above.

## Desktop

```text
DESKTOP_PUBLIC_RELEASE = PASS
GITHUB_RELEASE_URL = https://github.com/Luska-catjun/MINDCORE/releases/tag/v0.4.0
RELEASE_WORKFLOW_RUN_ID = 37874813846
INSTALLER = MindCore_0.4.0_x64-setup.exe
INSTALLER_SIZE = 30579594 bytes
INSTALLER_SHA256 = 2cb1cf8cd0230b07805ba5aff9dc80ffc301c7a11dbee6355768fd8a3c744c96
SHA256SUMS_MATCH = TRUE
UPDATER_SIGNATURE = PASS
LATEST_JSON = https://github.com/Luska-catjun/MINDCORE/releases/latest/download/latest.json
LATEST_JSON_VERSION = 0.4.0
LATEST_JSON_WINDOWS_X86_64 = PASS
PUBLIC_DOWNLOAD_CHECK = PASS / ANONYMOUS HTTPS / HTTP 200
```

[Release workflow](https://github.com/Luska-catjun/MINDCORE/actions/runs/37874813846) ran from the exact tagged dc2b5f6 source and completed successfully.

| Gate | Actual result |
| --- | --- |
| Source version consistency | PASS, 0.4.0 |
| Python portable suite | 650 discovered / 641 passed / 9 existing skips |
| Python compile | PASS |
| Frontend | 162 passed / 27 files |
| Updater configuration tests | PASS |
| Lint / frontend production build | PASS; existing 6 lint warnings retained |
| Windows sidecar / dependency boundary | PASS |
| Rust | 75 passed / 0 failed / 0 ignored |
| Windows cargo check | PASS |
| Production signing configuration | PASS |
| Signed NSIS build | PASS |
| Release metadata / SHA256SUMS / latest.json | PASS |
| Artifact upload / GitHub Release publication | PASS |

The existing `Validate signed build-only artifact` step is conditional on manual signed_build_only dispatch and was therefore skipped by the unchanged tag workflow. Tag execution performed its existing installer/nonempty-signature checks in `Create release metadata`. Independent verification of the actual public installer and signature subsequently passed using production public-key authority. No CI condition or assertion was altered for this release.

All four public release assets were downloaded again without credentials:

| Asset | Bytes | Result |
| --- | ---: | --- |
| MindCore_0.4.0_x64-setup.exe | 30579594 | HTTP 200, SHA256SUMS match |
| MindCore_0.4.0_x64-setup.exe.sig | 420 | HTTP 200, independent minisign verification PASS |
| latest.json | 1345 | HTTP 200, version/platform/URL/signature/canonical notes match |
| SHA256SUMS.txt | 94 | HTTP 200, installer digest match |

The independently downloaded installer signature reported `Signature and comment signature verified`. The public latest endpoint returned the same JSON as the v0.4.0 asset, SHA256 `cccf9180bd695456d78a928644504b38cd1cc0dd00b11e64396b0e13e7c4cd86`. Its windows-x86_64 entry points to the actual public v0.4.0 installer and uses the public `.sig` value. No development endpoint or wrong repository was present.

No Windows installation/runtime updater E2E was run on this macOS host. “Signed” here verifies the existing Tauri updater signing authority; separate Windows Authenticode validation was not performed.

## Android

```text
ANDROID_PUBLIC_RELEASE = PASS
ANDROID_GITHUB_RELEASE_URL = https://github.com/Luska-catjun/MINDCORE_Android.ver/releases/tag/android-v0.1.0
ANDROID_VERSION_NAME = 0.1.0
ANDROID_VERSION_CODE = 3
ANDROID_PACKAGE = com.luskacat.mindcore.android
ANDROID_APK = https://github.com/Luska-catjun/MINDCORE_Android.ver/releases/download/android-v0.1.0/MindCore-Android-0.1.0.apk
ANDROID_APK_SIZE = 23705333 bytes
ANDROID_APK_SHA256 = bbdd9b971fb8b785d4b40efb25d1f901d5fde265a24515c5468404e5c7b86c03
ANDROID_SIGNING_CERT_SHA256 = 53e1664cf9740078a31b86f46f7511c0827623ae3cab6cfe091d9672c5f50466
ANDROID_SIGNING_CERT_MATCH = PASS / PERMANENT KEY
ANDROID_MANIFEST = https://github.com/Luska-catjun/MINDCORE_Android.ver/releases/download/android-channel/mindcore-android-latest.json
ANDROID_PUBLIC_DOWNLOAD_CHECK = PASS / ANONYMOUS HTTPS / HTTP 200
ANDROID_PUBLIC_UPDATE_E2E = PARTIAL
ANDROID_PACKAGE_MANAGER_UPGRADE = PASS / CODE 2 TO CODE 3
ANDROID_SYNTHETIC_DATA_PRESERVATION = PASS
ANDROID_NEW_CHANNEL_RUNTIME_CHECK = PASS / UP_TO_DATE
```

### Contract and artifact verification

- Production manifest schema remains `versionName`, `versionCode`, `apkUrl`, `sha256`, `releaseNotes`, with existing optional fields unchanged. Upgrade eligibility uses versionCode.
- Existing transport policy remains HTTPS only, up to 5 redirects, no credentials, manifest size limit 1 MiB and APK size limit 512 MiB.
- Existing verification remains APK SHA256, matching installed package, manifest/APK versionCode equality, newer versionCode, and equal nonempty signing certificate sets. Missing/network-failed update checks do not block ordinary app use.
- Before migration, the original accepted APK was revalidated and publicly downloaded successfully with SHA256 `7a8d95dc06fe4085a9caba37c9541c53e4330ed82651ee0b320a529f62849957` and the same permanent certificate. That original versioned public asset remains unchanged at `MINDCORE/releases/tag/android-v0.1.0`. Its local bundle is preserved under Android `.toolchain/public-repo-migration/original-published-0.1.0/`.
- URL migration validation: release JVM/unit tests **37 passed / 0 failed / 0 skipped**; release compile/unsigned assemble PASS; new channel embedded, old channel absent, feedback endpoint retained.
- New same-key signed pipeline: preflight PASS, assembleRelease PASS, APK_VERIFY PASS, 8 native libraries, 16 KiB ELF/ZIP alignment PASS, release bundle secret scan PASS. A first hidden key-password entry failed with `Password is not ASCII`; after the user's new hidden entry the pipeline passed. No password or key content was disclosed.
- New public APK, manifest, SHA256SUMS, and release-notes.md were downloaded anonymously. APK SHA256, permanent certificate, package, version, checksums, canonical notes, and embedded production endpoints all matched the new local signed artifact.
- Canonical Android notes remain unchanged. Manifest notes use the existing generator's `.strip()` behavior; the public notes file is byte-identical to `release-notes/0.1.0.md`.

### Channel migration and compatibility

The dedicated `MINDCORE_Android.ver` version release is its latest stable release. Its `android-channel` is a prerelease so that the channel metadata does not replace the stable APK release as latest.

The old stable channel URL, `https://github.com/Luska-catjun/MINDCORE/releases/download/android-channel/mindcore-android-latest.json`, now serves the same new manifest as the dedicated repository, pointing to the new APK and digest. Both URLs returned HTTP 200 anonymously with identical JSON and SHA256 `ce8aa8ce1eee16f04dfff4d05dc7f6b23386f47e5d3010125d8c60cfc6d311fd`. This metadata bridge preserves older installed apps' channel reachability. The old immutable versioned APK, release tag, and evidence are preserved.

An already-installed original code 3 APK will not be offered another code 3 APK automatically. Such installations need a manual same-key reinstall of the dedicated-repository APK to embed the new channel URL. VersionCode was not silently incremented to bypass this rule.

### Actual update/upgrade evidence and limits

An isolated test-owned API 35 arm64 AVD, `MindCore_PublicRelease_API_35` / `emulator-5560`, was created without touching a user's AVD. The prior same-key APK (versionName 0.4.1, code 2, SHA256 `68798ff665437582bf677e7b545701bfd6a4921094a54e09f4a70f631bf70925`) was installed with synthetic data only.

1. The older app's actual production check showed `UPDATE_AVAILABLE` and 0.1.0 (3), with canonical public release notes.
2. Its actual HTTPS download produced the first public APK with the exact accepted `7a8d95dc…` digest.
3. During progress-event UI rendering, the older code 2 app crashed with `OutOfMemoryError` at a 201326592-byte heap growth limit, in `MainActivity.renderSettings` / `onUpdaterEvent`. READY_TO_INSTALL, package/certificate verification completion, and installer handoff were not observed. Full UI update E2E is consequently PARTIAL, not PASS. Attribution to a current code 3 regression is NOT_PROVEN; the faulting process was the older code 2 app.
4. After the authorized repository migration, the new APK was downloaded anonymously from the dedicated public release and installed with `adb install -r` through Android PackageManager. This is a separate installation proof, not an assertion that the app's installer handoff passed.
5. Actual installed version became 0.1.0 / code 3; pulled installed `base.apk` SHA256 exactly matched `bbdd9b97…`. Persona registry/selection, identity, Persona config, synthetic upgrade marker, schema metadata, and the two messages with raw roles `user`/`diana` were preserved.
6. The new app launched with the preserved synthetic Persona. Its real manual update check against the embedded dedicated channel reached `UP_TO_DATE`.

No provider request, real user data, or cross-device Persona continuity was tested. The isolated AVD was shut down after evidence capture. No updater/UI memory fix was attempted as part of deployment.

## Feedback

```text
DESKTOP_FEEDBACK_ENDPOINT = https://mindcore-feedback.nibung.workers.dev/feedback
ANDROID_FEEDBACK_ENDPOINT = https://mindcore-feedback.nibung.workers.dev/feedback
FEEDBACK_PRODUCTION_PATH = PASS / ARTIFACT EMBEDDING + EXISTING ACCEPTED EVIDENCE
FEEDBACK_HEALTH = HTTP 200 / mailConfigured=true
FEEDBACK_MAIL_RESEND = NOT_RUN / NOT REQUIRED
MONTHLY_REQUIRED_COST = 0
SMTP = NOT_USED
CLOUDFLARE_WORKERS_FREE = TRUE
GMAIL_API = TRUE
```

The actual public Windows installer was extracted. Its native executable contains the exact Brotli-compressed production frontend asset whose source includes the accepted endpoint, and contains the production Desktop updater endpoint. The public Android APK DEX contains the accepted feedback endpoint and dedicated Android updater URL. No production feedback configuration was changed.

Anonymous curl `/health` returned 200 and `mailConfigured=true`. An initial Python urllib request returned 403; the cause was not established. No new Gmail mail was sent; earlier user-confirmed Desktop/Android receipt evidence is retained. No OAuth or signing secret value was read or printed.

## Distribution

```text
REMOTE_TAG = PASS / v0.4.0 resolves to dc2b5f634ff86702978719581966f53b7dde5054
REMOTE_RELEASE = PASS / PUBLIC v0.4.0 AND android-v0.1.0
PUBLIC_UPDATER_DELIVERY = PASS / DESKTOP latest.json + NEW ANDROID CHANNEL + LEGACY BRIDGE
WINDOWS_PUBLIC_DOWNLOAD = PASS
ANDROID_PUBLIC_DOWNLOAD = PASS
DESKTOP_REPOSITORY_LATEST_RELEASE = v0.4.0
ANDROID_REPOSITORY_LATEST_RELEASE = android-v0.1.0
FORCE_PUSH_OR_HISTORY_REWRITE = NONE
TAG_REWRITE_OR_DELETION = NONE
PRODUCTION_ARCHITECTURE_CHANGE = NONE
```

Public Desktop latest delivery was checked again after both Android publications and channel migration. It still resolves to v0.4.0 and the verified Desktop latest.json. Android releases in the Desktop repository use latest=false. Windows release/signing/updater architecture is unchanged.

Local public downloads, CI logs, independent signature logs, contract inspection, APK identities, UI XML snapshots, OOM evidence, same-key upgrade/data comparisons, and screenshot are retained under Desktop `.toolchain/public-release-deployment/`; migration evidence is in its `android-migration/` directory. These build/evidence files are ignored, not committed. Canonical release notes and previous readiness/audit documents are preserved.

## Remaining acceptance limits

- Full Android UI installer-handoff E2E remains PARTIAL because of the observed older code 2 OOM. Actual public APK installation and data preservation passed separately.
- Current code 3 behavior during downloading a future higher-code update was not tested by a same-version manifest. No claim of fixing or disproving the older download-render OOM is made.
- Existing code 3 installations need manual installation of the URL-migrated APK; automatic upgrade cannot trigger for an equal versionCode.
- Windows installed-app updater E2E and Authenticode verification were NOT_RUN. Public delivery, updater signing authority, installer hashes and tag CI were verified.
