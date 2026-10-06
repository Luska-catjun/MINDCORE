# MindCore Desktop 0.4.0 Product UX / Feedback / Update Closure

검증일: 2026-10-06 (Asia/Seoul). IMPLEMENTED != ACCEPTANCE-PROVEN.

## 범위와 기준

- 저장소: `/Users/noseunghudong-alibujang/Developer/mindcore-desktop`
- branch: `backup/mindcore-desktop-current-2026-09-30`
- 시작 HEAD: `aad9cef37f284104b701b4a396b42e38a4607592`
- 시작 상태: tracked clean, 기존 untracked `.toolchain/` 보존, `git diff --check` 통과.
- 구현 commit: `5291a9f8fd04b15f5887d7c51f4eeb0690175bf9`
- 기존 credential LF/CRLF hotfix commit `aad9cef`를 유지했다.
- LOCAL_ONLY_PERSONA / 단일 authoritative SHARED_PERSONA DB 모델 유지. Legacy replica/sync 기반 확장 없음.
- push / tag / GitHub Release publish 모두 수행하지 않았다.

## 판정

```text
DESKTOP_PRODUCT_UX=PASS
DESKTOP_VERSION=0.4.0
DB_CREDENTIAL_CRLF_FIX=TRUE
TRAILING_LF_REPRODUCED=YES
TRAILING_CRLF_HANDLED=YES
PERSONA_DB_HEALTH=PASS
PERSONA_BINDING=BOUND_MATCH
SETUP_FEEDBACK=TRUE
IN_APP_FEEDBACK=TRUE
CURRENT_RELEASE_NOTES=TRUE
MARKDOWN_LINKS=TRUE
UPDATE_RECOMMENDATION_MODAL=TRUE
UPDATE_CAN_BE_DECLINED=TRUE
DIRECT_FEEDBACK_TRANSPORT=NOT_CONFIGURED
GITHUB_FEEDBACK_FALLBACK=TRUE
NEW_PRODUCT_REGRESSIONS=0
```

LF 재현은 이전 설치본에서 확인된 원인과 synthetic Rust regression으로 입증했다. 실제 secret에 줄바꿈을 다시 넣어 production 장애를 유발하지 않았다. `trim_end_matches(['\r', '\n'])`만 적용하며 선두/내부 문자와 의미 있는 공백을 보존한다.

## 구현

### Feedback

`ProductSupport`가 setup/boot/workspace 전환과 독립적으로 유지된다. Persona DB/백엔드/provider가 없는 첫 실행에서도 Feedback을 열 수 있다. 분류는 버그 / 기능 제안 / 사용성/UI / 기타, 제목·내용과 기본값 OFF인 진단 정보 동의, 전송 전 미리보기를 제공한다.

`frontend/src/product-support.json`은 공개 설정만 담는다. 운영 중인 secure feedback endpoint/relay를 찾지 못했으므로 `feedbackEndpoint: null`이다. 기본 경로는 공개 저장소 `https://github.com/Luska-catjun/MINDCORE/issues/new`의 작성 화면을 시스템 브라우저로 여는 것이다. 앱은 브라우저 열기를 제출 성공으로 표시하지 않는다. 실제 제출은 사용자가 GitHub에서 수행한다.

HTTPS direct POST abstraction은 구현했으며 mock 성공/오류를 검증했다. 실제 서버 전송 acceptance는 NOT_CONFIGURED이다. 쿠키·Authorization 전달 없음, redirect 거부, 15초 timeout, 일반 오류 문구 및 재시도, 오류 시 draft 유지. 자동 영구 queue는 없다.

동의할 때만 allowlist인 platform / app_version / os_version / provider / persona_connection / update_state를 미리보기에 추가한다. 전체 config 또는 Persona 객체를 payload로 사용하지 않는다. API key, DB token/full URL, Identity, 대화, Memory, signing 정보는 자동 포함하지 않는다. 사용자가 직접 입력한 제목·내용은 미리보기 후 전송 대상이 된다.

### Markdown / 업데이트

- source of truth: `release-notes/0.4.0.md`.
- `desktop/prepare-release-notes.mjs`가 package version과 파일/첫 heading/64 KiB 제한을 확인한 뒤 ignored JSON을 생성한다.
- dev/test/build에서 자동 생성한다. 현재 notes는 frontend bundle에 포함되어 외부 요청 없이 표시된다.
- 기존 Windows release workflow의 updater manifest `notes`와 release 본문도 같은 Markdown을 읽는다. 이 workflow는 이번에 실행/게시하지 않았다.
- `react-markdown` 10.1.0, raw HTML skip, 이미지 미렌더링. heading/paragraph/bullet/bold/inline code/link 지원.
- 링크는 frontend와 Rust 양쪽에서 HTTP/HTTPS 및 credential 없는 host를 검사하고 OS browser로 연다. `javascript:`, `file:`, `data:` 등은 차단한다.
- 원본 공지는 Markdown 링크로 작성할 수 있으며 dedicated optional announcementUrl도 안전한 URL일 때만 표시한다. 필드 부재/빈 remote notes는 안전하게 처리한다.
- 기존 Tauri updater service/plugin의 download/verification/stop/install/relaunch 경로 유지. 새 권장 모달은 현재/새 버전, summary, 업데이트 내용 보기, 나중에, 업데이트를 제공한다.
- 자동 설치하지 않는다. 다운로드 후 설치 및 다시 시작도 명시적 선택이다. 같은 세션에서 거절한 버전의 자동 popup을 억제하고 수동 확인으로 다시 열 수 있다.
- ready 이후 background check, 서버 오류가 setup/boot/대화를 막지 않는다.
- 기존 A2 navy/blue/cyan/teal 스타일을 재사용하고 새 용어를 Android와 맞췄다. Sidebar의 raw Debug 진입 버튼을 제거했다. 기존 domain panel 전체 번역은 범위에 포함하지 않았다.

## 테스트 및 빌드

| 항목 | 실제 결과 |
|---|---|
| `.venv/bin/python -m pytest` | 654 passed / 3 skipped / 380 subtests passed / warning 1 |
| Python compileall (`app desktop`) | PASS |
| `npm test` | 21 files / 118 passed |
| release notes + updater config Node tests | 4 passed |
| `cargo test --locked` | 63 passed / 0 failed |
| `cargo check --locked` | PASS |
| `scripts/release_guard.py version --expected 0.4.0` | PASS |
| packaged Python sidecar build / dependency boundary | PASS |
| `npm run build` | PASS |
| fresh Tauri `.app` build | PASS |

Frontend 최초 external raw Markdown import는 Vite filesystem sandbox에서 실패했다. repo build generation 방식으로 수정 후 전체 suite와 production build를 통과했다. 최종 build의 dynamic-import chunk warning은 build 실패가 아니다. Python warning은 기존 Starlette/AnyIO deprecation이다.

로컬 패키지 명령:

```sh
cd frontend
npm run desktop:sidecar:build
npm run build
npx tauri build --bundles app --config src-tauri/tauri.updater.conf.json --config '{"bundle":{"createUpdaterArtifacts":false}}'
```

CFBundleShortVersionString, Cargo, Tauri, frontend, FastAPI package metadata는 0.4.0. Native updater current version도 설치 UI에서 0.4.0을 확인했다. updater plugin/pubkey/config를 보존했다. 이번 로컬 `.app`은 public updater 배포용 signed archive 또는 notarized release acceptance가 아니다.

## 정확한 설치본 및 first-run smoke

- fresh artifact: `frontend/src-tauri/target/release/bundle/macos/MindCore.app`.
- 설치: `/Applications/MindCore.app` (0.4.0). Build와 installed native binary SHA256 일치.
- 이전 앱: `/Applications/MindCore-before-product-ux-20261006.app`에 보존. 기존 `MindCore-old.app`도 보존.
- `/health`: `status=ok`, `db=connected`.
- authenticated `/auth/me`: 기존 Persona 유지, `persona_binding_state=BOUND_MATCH`.
- 기존 conversation 1개 / message 4개 GET으로 읽기 확인. 신규 대화/메시지 생성 없음.
- 설치 UI의 버전, Feedback default opt-out, Markdown notes 모달, 업데이트 확인의 안전한 오류 표시 확인.
- `.toolchain/product-ux/isolated-desktop-setup/mindcore.env` override로 빈 설정 첫 실행 검증. production config/Identity/DB credential 복사 없음.
- DB/provider/Persona 없이 Feedback 작성/미리보기 가능, 미리보기 기본값에 diagnostics 없음, bundled notes 표시.
- Markdown 공지 클릭 후 시스템 Chrome에 공개 releases URL이 열린 것을 확인.
- 격리 앱 종료 후 정상 설정으로 복귀. `/health`, BOUND_MATCH, 기존 대화 재확인.
- production `mindcore.env`, `personas.json`, selected persona env, Identity의 baseline SHA256 모두 불변.
- UI smoke에서 Feedback 외부 제출 없음.

실제 updater endpoint는 보존된 개발 placeholder (`updates.mindcore.invalid`)이므로 네트워크 확인 성공/공개 업데이트 설치는 입증하지 않았다. 권장 모달·거절·manual reopen·기존 native service handoff는 사용자 요청대로 updater seam/mock으로 PASS. 실제 설치본은 endpoint 실패에도 정상 사용 가능한 것을 확인했다.

## 보안

```text
EMBEDDED_FEEDBACK_SECRETS=0
EMBEDDED_GITHUB_PAT=0
FEEDBACK_CONTAINS_API_KEY=0
FEEDBACK_CONTAINS_DB_TOKEN=0
FEEDBACK_SECRET_LEAKS=0
RELEASE_NOTES_SCRIPT_EXECUTION=NO
UNSAFE_LINK_SCHEMES_ALLOWED=NO
SENSITIVE_DATA_AUTO_INCLUDED=NO
```

Frontend bundle/native executable의 PAT/API key/private-key 패턴 및 알려진 현재 plaintext 설정 secret의 값 일치 검사에서 0건. 실제 값은 출력/보고서 저장하지 않았다. `.app` 안에 env/Persona registry/Identity 파일 없음. payload allowlist 및 opt-out 테스트 통과. 이는 사용자 자유 입력의 내용을 자동으로 익명화한다는 보장은 아니다.

## 근거 및 남은 운영 설정

로컬 evidence: `.toolchain/product-ux/`의 tests/build logs, installed-health-final.json, config-preservation.json, desktop-ui-smoke.json, security-scan.json. 해당 evidence는 commit에 포함하지 않는다. 보고서는 secret/개인 대화 내용 없이 판정과 수량만 기록한다.

다음 릴리즈 전에 `release-notes/<version>.md`를 작성하고 필요하면 원본 공지 링크를 추가한 뒤 기존 release pipeline을 실행하면 된다. 공개 HTTPS Feedback relay를 운영하게 되면 공개 endpoint만 설정한다. Desktop 실제 업데이트 배포는 유효 endpoint와 updater signing/publication을 별도 지시로 진행해야 한다.

NEXT_DECISION=FEEDBACK_ENDPOINT_SETUP
REMOTE_PUSH=NO
REMOTE_TAG=NO
REMOTE_RELEASE=NO
