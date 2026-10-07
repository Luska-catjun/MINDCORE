# V0.4.0 Feedback Transport Preparation Report

검증일: 2026-10-07 KST. 기준 HEAD: `6112b518f918efa7906799c19b61a561fe957a19`. branch: `backup/mindcore-desktop-current-2026-09-30`.

## Desktop 구현

기존 productSupport/FeedbackDialog를 Setup과 in-app에서 공유한다. 공개 설정은 `VITE_MINDCORE_FEEDBACK_ENDPOINT`이며 기존 product-support.json 값은 null이다. Vite build 설정과 submit boundary 모두 HTTPS DNS URL을 검증한다. userinfo/query/fragment/IP/local suffix는 거부한다. 실제 공개 endpoint는 미설정이다.

사용자에게 “MindCore 개발자에게 피드백을 보냅니다.”를 표시하고 endpoint 미구성/GitHub 최종 제출을 안내한다. configured 상태에서는 직접 전송을 우선 표시한다. 실패 시 동일 초안과 미리보기를 유지하고 retry/edit를 지원한다.

## 검증 결과

| 항목 | 실제 결과 |
| --- | --- |
| frontend 전체 | **155 PASS**, 25 files, FAIL 0 |
| feedback focused | **33 PASS**, 3 files, FAIL 0 |
| production frontend | TypeScript + Vite **PASS** |
| 공개 endpoint 설정 dry validation | mock public HTTPS build config ACCEPTED |
| 잘못된 endpoint 설정 | HTTP localhost build config REJECTED |
| Rust/Python | NOT APPLICABLE — 변경 없음 |
| .app rebuild | NOT APPLICABLE — native config/권한 변경 없음, 기존 CSP는 HTTPS 지원 |
| installed app 교체 | NOT RUN — 이번 요청에 필요 없음 |
| 신규 product regression | 최종 실행한 검증 범위에서 **0** |

명령:

```sh
npm --prefix frontend test -- --run --maxWorkers=2
npm --prefix frontend test -- --run src/services/productSupport.test.ts src/components/FeedbackDialog.test.tsx src/components/ProductSupport.feedback.test.tsx --maxWorkers=2 --reporter=json --outputFile=../.toolchain/feedback-preparation/feedback-tests.json
npm --prefix frontend run build
```

HTTP/network/malformed response 실패 → UI error → draft 보존 → 재시도 성공을 실제 shared service + mock fetch로 검증했다. opt-in preview, key/value privacy 제외, unconfigured fallback≠sent, setup/workspace 동일 payload도 검증했다.

최초 전체 실행에서는 기존 App.desktopLifecycle 테스트 하나가 Retry 버튼을 기다리는 동안 timeout됐고 146 PASS/1 FAIL이었다. lifecycle production/test 코드는 변경하지 않았다. worker 수를 2로 제한한 재실행 및 최종 전체 실행은 모두 실패 없이 완료됐다. 최초 timeout의 정확한 원인은 확정하지 않았다. 설정 구현 중 NodeNext import extension 오류는 .js module specifier로 해결했고 최종 빌드는 PASS다. 기존 dynamic-import bundler warning은 남아 있다.

증거: `.toolchain/feedback-preparation/frontend-final.log`, `feedback-tests.json`, `frontend-build.log`, `endpoint-public.log`, `endpoint-invalid.log`.

## 최종 상태

```text
DESKTOP_FEEDBACK_UI=PASS
ANDROID_FEEDBACK_UI=PASS
CROSS_PLATFORM_FEEDBACK_SCHEMA=TRUE
DIRECT_ENDPOINT_CONFIGURABLE=TRUE
DIRECT_ENDPOINT_CONFIGURED=FALSE
GMAIL_RECIPIENT_IN_CLIENT=FALSE
MAIL_CREDENTIAL_IN_CLIENT=FALSE
SENSITIVE_PERSONA_DATA_AUTO_INCLUDED=FALSE
DIAGNOSTICS_OPT_IN=TRUE
FAILED_SUBMISSION_PRESERVES_INPUT=TRUE
GITHUB_FALLBACK_OPENED_IS_SUCCESS=FALSE
SUBJECT_PREFIX_CONTRACT=[마인드코어 피드백]
NEW_FEEDBACK_SECRET_LEAKS=0
DESKTOP_VERSION=0.4.0
ANDROID_VERSION_NAME=0.1.0
ANDROID_VERSION_CODE=3
REMOTE_PUSH=NO
REMOTE_TAG=NO
REMOTE_RELEASE=NO
NEXT_DECISION=DEPLOY_FEEDBACK_ENDPOINT
```

## 계약 및 범위

[공통 전송 계약](FEEDBACK_TRANSPORT_CONTRACT.md)은 양 저장소에서 byte-identical하다. schemaVersion 1, 분류 enum, platform/appVersion, opt-in diagnostics, 제목 160/내용 6000 제한을 통일했다. HTTP 200/202와 bounded JSON boolean ok:true만 접수 성공으로 인정하며 임의 2xx/HTML/redirect/비표준 JSON/중복 필드/잘못된 ID를 거부한다. response 최대 4096 bytes, request 최대 32768 bytes이다.

진단 key/value allowlist로 Identity/Persona/private contents, API key, DB URL/token, 메일/서명 credential, 파일 경로와 raw exception을 자동 포함하지 않는다. 사용자가 직접 쓴 제목/본문은 미리보기 그대로 전송하는 정책이다. GitHub compose를 여는 상태는 sent와 구분하고 구성된 endpoint가 실패해도 자동 fallback하지 않는다.

이번 검증은 mock fetch/native connection과 synthetic fixtures만 사용했다. 실제 endpoint/recipient/credential 생성·설정, Gmail 발송, 서버 배포는 하지 않았다. **IMPLEMENTED != ACCEPTANCE-PROVEN**: 실제 public endpoint의 CORS, abuse 방어, durable 접수, Gmail delivery acceptance는 미래 배포 후 검증해야 한다. 서버가 recipient와 credential을 관리하고 `[마인드코어 피드백]` prefix를 포함한 제목을 작성해야 한다.

navy/blue/cyan/teal 스타일, 기존 네 개 primary navigation, 브랜드 icon을 보존했고 footer 피드백 중복을 추가하지 않았다. 사용자 Persona/DB/config 및 설치된 Desktop 앱, 사용자용 Android AVD는 변경하지 않았다. Legacy sync 및 cognition/shared-runtime 아키텍처와 production dependency/lockfile은 변경하지 않았다.

## Secret scan

변경 소스와 새 문서를 baseline HEAD와 비교하고 production build 산출물을 스캔했다. Gmail 주소, GitHub PAT, 값이 할당된 Gmail/OAuth/refresh-token/mail-API/feedback credential, private-key payload 패턴을 검사하고 값은 출력하지 않았다. 기존 public/library 문자열은 fingerprint로 baseline과 구분했으며 신규 노출 0건이다. 이 검사는 지정 패턴과 변경 범위의 증거이며 서버 mail delivery 검증을 대신하지 않는다. 증거: `.toolchain/feedback-preparation/secret-audit.json`, `audit.py`(Desktop 작업 폴더).

## Git 및 다음 단계

현재 backup branch를 유지하고 기존 dirty work를 보존했다. 허용된 로컬 commit만 사용한다. 로그/build output과 기존 Desktop `.toolchain/`은 stage하지 않는다. 원격 push/tag/release는 하지 않는다.

다음 결정은 **DEPLOY_FEEDBACK_ENDPOINT**. 실제 서버를 계약대로 구성하고 검증한 뒤 양 앱의 공개 endpoint build 설정을 넣어 재빌드한다. 이번 작업에서는 이 단계를 실행하지 않았다.
