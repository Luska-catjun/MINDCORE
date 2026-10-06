# MindCore Desktop 0.4.0 Navigation / Information Architecture

Date: 2026-10-07 (Asia/Seoul)
Implementation commit: `25c3359930ab713bd4d181d14171ebd55f3722ff`
Previous accepted baseline: `727812dfffadcd0ad13da6528afb7c165f908ed4`

## Scope and outcome

The primary navigation is exactly **대화 / 데이터 관리 / 앱 설정 / 피드백**.
The long domain sidebar is removed. Existing WorkspaceView identifiers, observation
API calls, native Persona commands, message lifecycle, feedback transport and updater
installation flow are retained. Python/Rust production source and dependencies are unchanged.
No replica/delta/conflict/offline Persona architecture is introduced.

## Information architecture

| Data category | Existing destinations |
| --- | --- |
| 대화 데이터 | Messages |
| 기억 | Memory, Episodes, Narrative |
| 상태 | Emotion, Goals & Needs, Decisions, Intentions |
| 지식 | Knowledge, World Model |
| 관계 | Relationship, Preferences |
| 자아 | Self Model |
| 진단 | Stats, Debug |

All 15 domains are reachable through compact navigation in the content area.
Settings has Persona / 연결 / AI 제공자 / 업데이트 / 일반 tabs.
Add Persona, Manage Personas, Identity and Reconfigure controls moved from the header
to settings; configuration folder commands remain accessible in Provider/General.
The global Persona selector remains. Settings invokes the existing persistent updater
and Feedback dialog; setup support also remains available.

The selected conversation and unsent draft survive product navigation. Data selection,
per-category selection and the mounted data surface remain across product sections.
Settings selection remains mounted. Persona switching still invalidates the previous
Persona's chat state. Chat remount retains its existing durable-history refresh behavior.

## Executed evidence

| Check | Result |
| --- | --- |
| Frontend `npm --prefix frontend test -- --run` | 124 passed, 23 files, 0 failed |
| Relevant Python auth/DB credentials/messages/isolation/sidecar tests | 39 passed, 4 subtests passed |
| Rust `cargo test --locked --manifest-path frontend/src-tauri/Cargo.toml` | 63 passed, 0 failed |
| TypeScript/Vite production build | PASS |
| Tauri production macOS app bundle, updater configuration retained | PASS |
| Installed native app: four primary entries, settings Persona controls | PASS |
| Installed native app: settings Connection / Connected / Persona connected | PASS |
| Installed native app: settings opens bundled 0.4.0 update notes | PASS |
| Installed native app: independent Feedback dialog, no submission | PASS |
| Installed native app: data > 진단 > Debug, data selection retained | PASS |
| Existing configuration file hashes before/after installation | 0 changed, 0 added |

Commands/logs are under local `.toolchain/navigation/` and are not committed.
Python command selected `tests/test_auth_credentials.py`,
`tests/test_persona_database_credentials.py`, `tests/services/test_repository_messages.py`,
`tests/services/test_multi_persona_isolation.py`, `tests/test_desktop_backend.py`.
The full cognition/runtime suite was not repeated for this UI change.

Initial frontend failures were two existing tests using old direct Episodes/Emotion
navigation. Their paths were updated; their behavioral assertions remain. Final suite
passes. One additional test checks that settings requests use the same updater instance
without replaying a handled request.

Live remote updater check still reports unavailable with the existing distribution
configuration; this work does not establish remote updater publication/delivery.
Feedback fallback/transport and install handoff remain covered by existing frontend tests.
IMPLEMENTED does not imply unexecuted release infrastructure acceptance.

## Visual and icon audit

Four primary entries, content subnavigation and settings use the existing navy/blue/cyan/
teal language with radius, spacing and focus treatment. Product UI source contains no
emoji/emoticons; user-supplied conversation text is not altered. Semantic status colors
are not legacy green branding.

Actual canonical product icon is `frontend/src-tauri/icons/icon.png` (512 × 512),
SHA-256 `49e23d6acfecd076b861521dd91c7ca65e01a51c85ea97aa4dd6d07a391fc62c`.
The historical `mindcore-icon-source.png` differs from the currently packaged icon and
was not chosen as the brand source. Desktop packaged icons are unchanged.
Android uses byte-identical canonical artwork, existing round artwork, adaptive inset
and legacy fallbacks; device/resource evidence is recorded in the Android report.

## Acceptance and versions

- TOP_LEVEL_NAV_COUNT = 4
- DESKTOP_LONG_SIDEBAR_REMOVED = TRUE
- DATA_DOMAIN_ACCESS_PRESERVED = TRUE
- DATA_SECONDARY_NAV = PASS
- SETTINGS_REORGANIZATION = PASS
- PERSONA_CONTROLS_PRESERVED = YES
- PERSONA_CONNECTION_ACCESS_PRESERVED = TRUE
- FEEDBACK_ACCESS_PRESERVED = TRUE
- UPDATER_ACCESS_PRESERVED = TRUE
- EMOJI_IN_PRODUCT_UI = 0
- LEGACY_GREEN_BRANDING = 0
- NAVY_BLUE_CYAN_TEAL_LANGUAGE = TRUE
- UI_COHERENCE = PASS
- NEW_PRODUCT_REGRESSIONS = 0 within the executed UI/relevant regression scope
- DESKTOP_VERSION = 0.4.0 unchanged
- REMOTE_PUSH = NO / REMOTE_TAG = NO / REMOTE_RELEASE = NO

Installed application: `/Applications/MindCore.app`.
Pre-navigation backup: `/Applications/MindCore-before-navigation-20261007.app`.
Existing Persona/database credentials and Identity contents are preserved; no secrets
are included in this report.
