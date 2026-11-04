# Cognito 로그인과 사용자별 작업 공간

React → Node 세션 → Cognito User Pool → RDS `a4b.users` 구조다. Cognito Managed login에서 회원가입·이메일 인증·로그인·비밀번호 재설정을 처리한다. 인증 코드는 PKCE S256, 브라우저별 state, ID 토큰 nonce로 검증한다. Node는 `aws-jwt-verify`로 서명·발급자·클라이언트·만료·토큰 종류를 검사한다.

브라우저에는 HttpOnly·SameSite=Lax 세션 쿠키만 전달한다. HTTPS에서는 Secure 및 `__Host-` 접두사를 적용한다. Access/ID/Refresh Token은 브라우저 저장소로 전달하지 않는다. 서버 메모리 세션이므로 서버 재시작 시 다시 로그인해야 하며, 현재 구성은 Node 프로세스 한 개를 전제로 한다.

## 1. AWS 계정 확인과 Cognito 생성

기존 RDS와 같은 AWS 계정에 로그인한다. Chrome 로그인과 AWS CLI 프로필의 계정이 같아야 한다. 프로필을 다른 계정으로 덮어쓰지 않는다.

```bash
aws login --profile a4b3400
aws sts get-caller-identity --profile a4b3400
node scripts/deploy/configure-cognito.mjs --profile a4b3400 --expected-account YOUR_12_DIGIT_ACCOUNT_ID --origin http://localhost:3041
```

스크립트는 계정 번호를 먼저 확인하고 CloudFormation `a4b-cognito` 스택을 배포한다. 이메일 로그인, 공개 앱 클라이언트(비밀 키 없음), Code 흐름, 15분 Access/ID Token, Refresh Token 회전, Managed login 기본 스타일을 만든다. 사용자 풀은 Essentials 플랜이며 사용량에 따른 AWS 요금이 적용된다. 기존 EC2·RDS·S3 및 네트워크 설정은 수정하지 않는다. 사용자 풀은 Retain과 삭제 방지로 보호한다.

콜백은 `서비스출처/auth/callback`, 로그아웃 복귀는 `서비스출처/`다. localhost와 127.0.0.1은 다른 출처이므로 설정한 주소로 접속한다. 생성된 `.env.cognito.local`에는 비밀이 아닌 Cognito 식별값이 저장되며 Git에서 제외된다. 재실행 시 기존 설정 파일은 덮어쓰지 않는다. CloudFormation Outputs에서 변경값을 확인한다.

로그인 요청에 `lang=ko`를 전달하므로 Cognito 회원가입·로그인·비밀번호 찾기 화면도 한국어로 표시된다.

## 2. 로컬 로그인 확인

로컬 주소로 생성한 설정은 명시적으로 SQLite 회원 저장소를 사용한다. 먼저 클라우드 로그인 자체를 확인하고, 배포 시에는 다음 절의 RDS 회원 저장소로 변경한다.

```bash
npm run build
node --env-file-if-exists=.env --env-file=.env.cognito.local Backend/api/build-server/index.mjs
```

`.env.cognito.local`이 지정한 주소에서 로그인·회원가입을 진행한다. 이메일 인증 후 작업 공간에 진입할 수 있다. 비밀번호·인증 코드는 사용자가 Cognito 화면에서 직접 입력한다. 이름이 없으면 검증된 이메일의 앞부분을 표시 이름으로 사용한다.

기존 로컬 데모는 `A4B_AUTH_MODE=off`로 유지된다. 이 모드는 로컬 사용 전용이며 공개 서비스 주소가 지정되면 시작을 거부한다. `cognito` 모드에서 필수값이 없으면 인증 없이 실행하지 않고 시작을 거부한다.

## 3. 기존 RDS 회원 저장소 연결

```dotenv
A4B_AUTH_MODE=cognito
EASYFLOW_PUBLIC_URL=https://YOUR_SERVICE_DOMAIN
A4B_COGNITO_USER_POOL_ID=ap-northeast-2_POOL_ID
A4B_COGNITO_CLIENT_ID=CLIENT_ID
A4B_COGNITO_DOMAIN=https://YOUR_DOMAIN.auth.ap-northeast-2.amazoncognito.com
A4B_AUTH_USER_STORE=postgres
A4B_DATABASE_URL=postgresql://APP_USER:URL_ENCODED_PASSWORD@RDS_HOST:5432/DB_NAME
A4B_DATABASE_CA_FILE=/absolute/path/to/global-bundle.pem
A4B_AUTH_SESSION_SECONDS=28800
```

인증서 번들은 [AWS RDS 공식 TLS 문서](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)의 `global-bundle.pem`을 사용한다. 인증서와 호스트 이름을 검증하며 `rejectUnauthorized=false`는 사용하지 않는다. DB URL에 `sslmode` 등 TLS 옵션을 넣으면 시작을 거부한다. 비밀번호는 URL 인코딩하고 실제 접속정보는 Git에 저장하지 않는다. DB 계정에는 `a4b.users`의 SELECT·INSERT 및 email/name UPDATE 권한이 필요하다. 백엔드에서 RDS에 연결할 네트워크 경로도 준비되어야 한다.

비공개 RDS에 SSH 포워딩으로 연결할 때는 DB URL의 호스트를 포워딩된 로컬 주소로 두고, `A4B_DATABASE_TLS_SERVER_NAME`에 실제 RDS 호스트 이름을 지정한다. 로컬 터널에서도 실제 RDS 인증서의 호스트 이름을 검증한다. RDS 공개 접속이나 보안 그룹을 확대할 필요가 없다.

기존 `a4b.users` 스키마를 사용하므로 로그인용 스키마 마이그레이션은 없다. `auth_provider=cognito:USER_POOL_ID`, `auth_subject=검증된 sub`로 회원을 식별한다. 여러 사용자 풀의 동일 sub를 구분한다. 이메일만 같다는 이유로 기존 계정에 연결하지 않는다. 같은 이메일의 기존 외부 인증 계정이 있으면 관리자가 소유 관계를 검토해야 한다. 첫 로그인은 기본 `member` 권한이다. 클라이언트가 role·status를 지정할 수 없다. 매 요청에서 회원의 활성 상태를 확인한다.

### DB 스키마 정의

실제 RDS에서 추출한 전체 정의를 [`infrastructure/db/schema.sql`](../infrastructure/db/schema.sql)에 보관한다. 2026-11-02 기준 PostgreSQL 16.15의 `a4b` 스키마이며, `a4b.users`를 포함한 테이블 19개·컬럼 198개·외래키 44개·함수 39개·트리거 25개와 인덱스·주석·소유권·권한을 담는다. 회원 행과 카탈로그 초기 데이터, 비밀번호·접속정보는 포함하지 않는다. 기존 DB의 증분 마이그레이션이나 앱 시작 시 실행할 초기화 SQL로 사용하지 않는다.

pgvector와 DB 역할은 `a4b` 밖의 선행 객체이므로 [`prerequisites.sql`](../infrastructure/db/prerequisites.sql)로 준비한다. PostgreSQL 16과 pgvector 0.8.2를 설치한 새 빈 DB에서, 소유권 설정과 확장 설치 권한이 있는 관리자가 psql 16.15 이상으로 실행한다. 접속 설정은 `PGSERVICE`·`PGPASSFILE` 등으로 외부에서 제공한다.

```bash
psql -X --set ON_ERROR_STOP=1 --single-transaction \
  --dbname=YOUR_EMPTY_DATABASE \
  --file=infrastructure/db/prerequisites.sql \
  --file=infrastructure/db/schema.sql
```

복원용 선행 SQL이 새로 만드는 역할은 `NOLOGIN`이며, 실제 앱 계정의 로그인·암호 설정은 배포 담당자가 별도로 관리한다. `schema.sql`은 `postgres` 및 `a4b_guard_owner` 소유권과 기존 ACL을 보존한다. 특히 SECURITY DEFINER 함수 5개의 전용 소유자, PUBLIC 함수 실행 권한 회수, 검사 이력의 변경 불가 권한을 함께 유지한다. 전체 서비스용 새 DB를 구성할 때는 카탈로그 기준 데이터도 별도로 준비해야 한다.

갱신할 때는 읽기 전용 RDS 연결에서 다음과 같이 정의만 추출하고, 추출 일자·버전 주석을 갱신한 뒤 빈 DB 복원과 로그인 회원 SQL을 검증한다. 소유권과 ACL을 포함하는 pg_dump 기본 추출 설정을 유지한다. 재실행 시 생성되는 `\restrict`/`\unrestrict` 키도 그대로 보관한다. [pg_dump 스키마 추출과 외부 의존성 안내](https://www.postgresql.org/docs/16/app-pgdump.html)

```bash
PGSERVICE=a4b_readonly pg_dump --schema-only --schema=a4b --no-password \
  --file=schema.dump.sql
```

2026-11-02 검증: 외부 네트워크가 없는 임시 PostgreSQL 16·pgvector 0.8.2 DB에 두 SQL을 복원하고 pg_dump 16.15로 재추출했다. 버전 헤더와 매번 생성되는 psql 제한 키만 제외한 `a4b` 정의·소유권·ACL 전체가 실제 RDS 원본과 일치했다. 앱 역할의 회원 upsert·기본 member/active 값·재로그인 시 동일 UUID 유지, 검사 이력의 수정·삭제 차단, 보안 정의자 역할 분리도 확인했다. 합성 회원은 롤백했고 실제 RDS의 구조·데이터는 변경하지 않았다.

## 4. 사용자별 자료 분리 범위

회원 정보는 RDS에 저장한다. 작업 공간은 `EASYFLOW_DATA_DIR/users/RDS_MEMBER_UUID/` 폴더로 정한다. 에이전트 빌더 업무 데이터는 `A4B_STORAGE=postgres`일 때 같은 RDS의 `a4b_builder` 스키마에 그 회원 ID를 `workspace_id`로 넣어 저장하고, 행 수준 보안으로 다시 막는다([업무 데이터 저장소](business-data-storage.md)). 나머지 Node 업무 저장소는 계속 이 폴더에 격리한다.

- Node 워크플로우·실행 이력·암호화된 연결 계정 및 보호 키
- Python 에이전트·대화·업로드 문서·문서 검토·실행 보고서
- 파서 결과·원본 파일·예약 실행·LLM 체크포인트

작업 공간은 인증된 회원 ID로만 선택한다. 요청의 userId/ownerId 및 다른 사용자의 자료 ID로 저장소를 바꾸지 않는다. Python 작업에 동일한 사용자 디렉터리를 전달하고, 예약 실행과 실행 큐도 작업 공간별로 분리한다. 재시작 시 기존 사용자 디렉터리의 예약을 다시 시작한다. 기존 공유 루트 자료를 새 계정으로 자동 이전하지 않는다.

외부 서비스 OAuth의 토큰 갱신 잠금도 작업 공간별로 보관한다. 같은 회원의 동일 연결 요청은 한 번만 갱신하고, 다른 회원이 같은 연결 ID를 사용해도 토큰과 저장 결과를 공유하지 않는다. 같은 회원으로 페이지를 새로고침하면 저장된 설계·대화·선택 ID를 유지하고, 계정이 바뀌거나 로그아웃하면 지운다.

설계 대화의 자료 업로드·도구 선택·재설계 확인은 같은 회원·세션의 잠금을 공유한다. 진행 중인 변경 요청과 충돌하면 Python 작업을 시작하지 않고 409를 반환한다. 다른 회원의 같은 ID는 별도로 처리한다.

외부 Webhook은 `/hooks/MEMBER_UUID/WORKFLOW_ID/PATH` 주소를 사용한다. 로그인 쿠키 대신 기존 Webhook 토큰·인증 설정을 검사한다. 비활성 회원의 Webhook은 거부한다. 인증 없음으로 설정한 Webhook은 해당 사용자의 워크플로우를 외부에서 호출할 수 있으므로 워크플로우의 의도에 맞게 설정한다.

Webhook HTTP 응답은 SVG·XHTML·파일을 포함한 모든 MIME에 서버의 CSP `sandbox`와 `nosniff`를 강제한다. 스크립트 실행과 폼·팝업·다운로드는 허용하지만 `allow-same-origin`은 허용하지 않아 앱의 쿠키·저장소·비공개 API와 출처를 분리한다. 사용자 지정 CSP와 nosniff 헤더로 이 정책을 덮어쓸 수 없다. `Set-Cookie`·`Set-Cookie2`·`Clear-Site-Data` 응답 헤더는 대소문자와 관계없이 거부한다. CSP의 출처 격리 동작은 [MDN sandbox 설명](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox)을 참고한다.

로그아웃하면 서버 세션을 즉시 삭제하고 Refresh Token을 폐기한 뒤 Cognito 로그아웃 화면으로 이동한다. 복사한 세션 쿠키도 더 이상 사용할 수 없다. 관리자·회원 모두 자기 작업 공간에만 접근하며, 다른 회원 자료에 접근하는 관리자 UI는 추가하지 않는다.

로그아웃은 `/api/auth/logout`에 같은 출처의 JSON POST를 보낸 다음 반환된 Cognito 주소로 이동한다. `no-referrer` 정책에서 HTML 폼 POST의 Origin이 `null`이 되는 브라우저 동작에도 요청 출처 검사를 유지한다.

## 5. 배포와 검증

운영 도메인으로 CloudFormation의 PublicOrigin과 앱의 EASYFLOW_PUBLIC_URL을 함께 변경한다. HTTPS 프록시는 원래 Host를 전달해야 하며, 서버는 X-Forwarded-Host로 접속 출처를 변경하지 않는다. 변경 API와 로그아웃은 정확한 Origin을 요구한다. 인증 없이 조회 가능한 API는 `/api/auth/me`와 최소 상태만 반환하는 `/api/health`다.

Cognito 모드의 `GET /api/health`와 `HEAD /api/health`는 Host 검사 전에 `{ok:true,standalone:true,auth:"cognito"}`만 반환한다. ALB의 대상 IP Host로도 확인할 수 있으며, HEAD의 본문은 비어 있다. 대상 그룹 헬스체크 경로는 `/api/health`로 지정한다. 이 응답은 프로세스 생존 확인용이며 DB·모델 준비 상태를 검사하지 않는다. 다른 API와 로그인 경로는 기존 Host·Origin·인증 검사를 유지한다. [AWS ALB 헬스체크 Host 설명](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-troubleshooting.html)을 참고한다.

CloudFront를 앞에 둘 때는 [AllViewer origin request policy](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html#managed-origin-request-policy-all-viewer) 또는 동등한 정책으로 원래 Host·쿠키·쿼리를 전달한다. `/auth/*`와 `/api/*`는 캐싱을 비활성화하고 서비스 도메인을 EASYFLOW_PUBLIC_URL과 일치시킨다.

Google Sheets 등 외부 서비스의 OAuth 콜백은 `EASYFLOW_PUBLIC_URL/api/oauth/callback`이다. 공급자 콘솔의 승인된 리디렉션 URI에도 이 주소를 정확히 등록한다. Cognito의 `/auth/callback`과는 별도 주소다. 운영에서는 설정된 HTTPS 주소를 사용하며, 요청의 Host나 X-Forwarded-* 값으로 콜백 출처를 바꾸지 않는다. 인증을 끈 로컬 데모에서는 기존 localhost·127.0.0.1·IPv6 루프백 콜백을 유지한다.

서명된 테스트 JWT로 다음을 검증한다. 실제 Cognito 이메일 인증과 RDS 연결은 유효한 AWS 계정·리소스·DB 설정에서 별도로 완주해야 한다.

```bash
npm run check
npm run build
node --import tsx --test Backend/api/tests/cognito-auth.test.ts Backend/api/tests/oauth.test.ts Backend/api/tests/workspace-oauth.test.ts Backend/api/tests/agent-builder-locks.test.ts Backend/api/tests/webhook-response.test.ts Backend/api/tests/webhook-api.test.ts Frontend/tests/auth.test.ts Frontend/tests/frontend-dom.test.ts
```

두 사용자 간 워크플로우·연결 계정·실행/다운로드 접근 차단, 요청 출처 검사, 잘못된 토큰 거부, 콜백 재사용 방지, 동시 갱신, 로그아웃, 비활성 계정 접근 차단을 확인한다.

운영 배포 전에 다음 항목을 팀에서 결정한다.

- 메모리 세션·로그인 대기 상태는 Node 프로세스 한 개에서 사용한다. 재시작 시 재로그인이 필요하며 다중 인스턴스·프로세스 운영에는 공유 세션 저장소가 필요하다.
- `/auth/login`의 전역 대기 500개 제한만으로는 반복 요청을 막을 수 없다. 공개 배포 전에 WAF 또는 앱의 요청 제한을 적용한다. 앱에서 프록시 IP 헤더를 사용할 경우 신뢰하는 프록시에서 전달한 값만 사용한다.
- 현재 이메일 인증을 통한 공개 회원가입이 가능하다. 시연용 제한 가입과 공개 가입 중 운영 정책을 정하고, 허용 목록·관리자 계정 생성 또는 사용량 제한을 준비한다.
- 기본 Compose 포트는 127.0.0.1에 바인딩된다. ALB가 직접 연결하면 운영용 포트 설정과 보안 그룹을 준비하거나, EC2의 역방향 프록시를 통해 연결한다. 사용자별 SQLite·파일 볼륨은 EBS 등 영속 저장소에 두고 백업·복구 절차를 정한다.

## 실제 RDS 연결 검증 — 2026-11-02

기존 비공개 RDS의 `a4b_app` 계정으로 TLS 1.3 연결과 인증서 검증을 확인했다. 실제 Cognito 인증 후 `a4b.users`에 회원이 생성되고 기본 `member` 권한으로 작업 화면에 진입했으며, 로그아웃 후 재로그인해도 같은 회원 UUID가 유지되고 중복 행이 생기지 않는 것을 확인했다.

Docker의 `host.docker.internal`을 통해 SSH 포워딩에 연결할 때도 실제 RDS 인증서의 DNS 이름을 검증한다. `pg`가 TLS 옵션의 SNI를 TCP 호스트로 다시 지정하므로, 명시한 DB 이름에 대한 검사는 Node의 기본 `checkServerIdentity`로 수행한다. 인증서 체인 검증을 끄지 않으며, 다른 호스트 인증서를 거부하는 회귀 테스트가 있다.

검증용 Cognito 계정과 임시 비밀번호·DB 연결 환경 파일을 삭제했다. 앱 DB 계정에는 회원 DELETE 권한이 없으므로 검증용 RDS 회원은 비활성화로 정리했다. 사용자의 승인 조건에 따라 기존 EC2와 RDS를 다시 중지했고, 두 리소스 모두 실제 `stopped` 상태를 확인했다. 관련 회귀 테스트 121개가 통과했다. 리소스 최종 상태와 비밀값 없는 검증 결과는 Git에서 제외된 `runtime/cognito-review/`에 기록한다.

RDS가 중지된 동안 `http://localhost:3041`의 확인용 앱은 `.env.cognito.local`의 명시적인 SQLite 개발용 회원 저장소를 사용한다. 운영 시에는 RDS 실행 상태와 네트워크를 준비하고 `postgres` 저장소를 사용해야 한다. 로컬 SQLite 업무 데이터의 전체 RDS 이관은 별도 작업이다.
