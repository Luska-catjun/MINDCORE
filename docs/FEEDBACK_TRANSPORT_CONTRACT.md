# MindCore Feedback Transport Contract — schemaVersion 1

두 앱은 하나의 public HTTPS feedback endpoint를 사용한다. 실제 endpoint 및 서버는 아직 구성하지 않았다. 이 문서는 양 저장소에 동일하게 보관한다.

## 요청

`POST` + `Content-Type: application/json` (UTF-8).

```json
{"schemaVersion":1,"category":"bug","title":"Synthetic title","message":"Synthetic message","platform":"desktop","appVersion":"0.4.0"}
```

- `category`: `bug`(버그), `feature`(기능 제안), `usability`(사용성/UI), `other`(기타).
- `platform`: Desktop은 `desktop`, Android는 `android`.
- `appVersion`: 제품 manifest의 현재 버전. Desktop `0.4.0`, Android `0.1.0`/versionCode `3` 유지.
- 제목 1..160, 내용 1..6000 UTF-16 code units. 공백만으로 된 입력 거부, 앞뒤 공백 제거. 기존 UI 제한 유지.
- `diagnostics`는 선택 필드. 기본 OFF, 사용자가 체크하고 전송 전에 JSON 미리보기로 확인한다.
- 요청 전체 최대 32768 UTF-8 bytes, diagnostics 최대 1024 bytes (허용 값은 ASCII).
- 정의되지 않은 top-level/diagnostics 필드는 전송 단계에서도 거부한다.

## 진단 정보 허용 목록

키와 **값의 형식 모두** 제한한다. 나머지는 수집/전송하지 않는다.

| 키 | 허용 값 |
| --- | --- |
| osVersion | 숫자 버전 1..4 요소, 각 요소 1..3자리 |
| architecture | arm64, aarch64, arm64-v8a, x86_64, x64, x86 |
| provider | gemini, groq, anthropic, xai, openai |
| backendStatus | connected, disconnected, loading, error |
| personaConnection | BOUND_MATCH, UNBOUND, BOUND_MISMATCH, DB_UNAVAILABLE |
| updaterState | idle, checking, up-to-date, available, downloading, ready, installing, error 또는 Android 대문자 enum(UP_TO_DATE 포함) |

현재 UI가 제공할 수 있는 정보만 포함하고 빠진 항목을 임의로 채우지 않는다. platform/appVersion은 진단 동의와 무관한 필수 요청 metadata이다.

LLM API key, DB URL/token, 메일 주소/SMTP/OAuth/App Password/mail API credential, GitHub PAT, signing/keystore/updater private key는 자동 첨부하지 않는다. Identity, conversation/history, Memory, Experience, Episodes, Narrative, Self Model, Relationship, private Persona 내용/이름/사용자 파일 경로/예외 문자열도 제외한다. 로컬 DB/Identity/config를 읽어 피드백 body를 만들지 않는다. 사용자가 직접 작성한 제목/내용은 미리보기 그대로 보내므로 자동 진단 제외와 구분한다.

## 응답과 오류

성공은 HTTP **200 또는 202** + `Content-Type: application/json` + JSON boolean **`ok: true`**만 인정한다.

```json
{"ok":true,"feedbackId":"public_tracking_id"}
```

`feedbackId`는 선택이고 `[A-Za-z0-9_-]{1,128}`만 허용한다. 응답 필드는 `ok`, `feedbackId`뿐이다. 서버는 literal key names를 사용하고 두 필드의 순서는 자유이다. 최대 4096 UTF-8 bytes. HTML/빈 응답/임의 2xx/redirect/`ok: "true"`/`ok:false`/잘못된 JSON/비정상 ID는 실패다. 응답이나 ID는 사용자에게 그대로 표시하지 않는다.

HTTP/network/timeout/응답 검증 실패 시 `FAILED`. 폼과 미리보기 입력을 보존하며 재시도/수정할 수 있다. 예외 원문을 UI/log에 노출하지 않는다. 구성된 endpoint의 실패를 GitHub fallback으로 자동 전환하지 않는다. 자동 retry도 하지 않는다. 서버는 response 유실 후 사용자의 재시도로 중복 접수가 가능함을 고려해야 한다.

Desktop fetch: redirect error, credentials omit, 15초 AbortSignal. Android: native HttpURLConnection, redirect OFF, cache OFF, Authorization/Cookie를 설정하지 않음, connect/read 15초 및 응답 읽기 deadline/size bound. 이 timeout은 전송 서비스 한 번의 네트워크 작업 제한이며 Gmail 발송 대기 시간이 아니다.

`DIRECT_SENT`(코드의 sent/SENT)는 endpoint가 요청을 접수했다는 뜻이다. 최종 메일 delivery acceptance는 미래 서버의 책임이다. `FALLBACK_OPENED`(browser/BROWSER)는 작성 페이지를 열었다는 뜻이고 **sent가 아니다**.

## 공개 endpoint 설정

- Desktop: build/dev 환경의 `VITE_MINDCORE_FEEDBACK_ENDPOINT`. 예: `VITE_MINDCORE_FEEDBACK_ENDPOINT=<public HTTPS URL> npm --prefix frontend run build`. Vite 설정에서 malformed endpoint를 거부하고 전송 시에도 검사한다. 기존 product-support.json의 feedbackEndpoint는 null 유지.
- Android: Gradle `-PmindcoreFeedbackEndpoint=<public HTTPS URL>`, 또는 build 환경 `MINDCORE_FEEDBACK_ENDPOINT`. 기존 android/product-support.properties의 feedbackEndpoint는 빈 값 유지. property → 환경 → 기존 공개 설정 순서. BuildConfig.FEEDBACK_ENDPOINT에 공개 URL만 포함한다.
- HTTPS DNS host만 허용; userinfo, query 전체, fragment, IP literal, localhost/local/internal/test/invalid/example suffix 거부. 테스트는 HTTP 서버 접근 없이 mocked fetch/ConnectionFactory로 주입하므로 production 보안을 완화하지 않는다.
- endpoint URL 자체는 공개 설정이고 secret을 넣지 않는다. 실제 URL/credential을 이번 작업에서 설정하지 않았다.
- 미설정 시 앱은 계속 동작하며 기존 공개 GitHub compose fallback을 연다. 공개 전송 미구성 상태와 브라우저에서 최종 제출해야 함을 UI에 안내한다. 공개 issue에 올릴 내용도 사용자가 미리보기로 확인한다.
- Setup과 in-app은 Desktop productSupport/FeedbackDialog, Android FeedbackSupport/showFeedback의 동일 서비스/폼을 이용한다.

## 미래 서버 책임 (이번 작업에서 구현/배포하지 않음)

HTTPS, POST only, Content-Type 및 JSON schema 검증, body/field limits, rate limiting/abuse protection, 안전한 error와 로그, 공개 tracking ID, 서버 측 recipient와 credential 관리, Gmail delivery가 필요하다. Desktop WebView의 HTTPS POST에는 CORS/preflight가 필요하다. 허용된 앱 origin(플랫폼별 Tauri origin) 및 필요한 Content-Type을 지원하고 client credentials는 요구하지 않는다. CORS는 인증이 아니므로 서버가 abuse를 별도로 방어한다.

서버가 subject를 만든다: **`[마인드코어 피드백][Desktop][버그] <title>`** 또는 **`[마인드코어 피드백][Android][기능 제안] <title>`**. 권위 있는 prefix는 **`[마인드코어 피드백]`**. client는 category/platform/title을 구조화해서 전달하며 mail subject/recipient를 포함하지 않는다. 서버는 제목 CR/LF 등 mail header injection도 방지한다.

Gmail SMTP + App Password, Gmail API + OAuth credential, 신뢰할 수 있는 mail delivery API 중 hosting에 적합한 방법을 나중에 선택한다. recipient, credential, refresh token은 **server secret only**. 본문/Persona/secret을 로그에 남기지 않고 접수 실패와 delivery 실패를 안전하게 처리한다. 202를 반환하려면 유실 없이 처리할 durable 접수 정책도 마련해야 한다.
