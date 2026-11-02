# Cognito 로그인과 사용자별 작업 공간

React → Node 세션 → Cognito User Pool → RDS `a4b.users` 구조다. Cognito Managed login에서 회원가입·이메일 인증·로그인·비밀번호 재설정을 처리한다. 인증 코드는 PKCE S256, 브라우저별 state, ID 토큰 nonce로 검증한다. Node는 `aws-jwt-verify`로 서명·발급자·클라이언트·만료·토큰 종류를 검사한다.

브라우저에는 HttpOnly·SameSite=Lax 세션 쿠키만 전달한다. HTTPS에서는 Secure 및 `__Host-` 접두사를 적용한다. Access/ID/Refresh Token은 브라우저 저장소로 전달하지 않는다. 서버 메모리 세션이므로 서버 재시작 시 다시 로그인해야 하며, 현재 구성은 Node 프로세스 한 개를 전제로 한다.

## 1. AWS 계정 확인과 Cognito 생성

기존 RDS와 같은 AWS 계정에 로그인한다. Chrome 로그인과 AWS CLI 프로필의 계정이 같아야 한다. 프로필을 다른 계정으로 덮어쓰지 않는다.

```bash
aws login --profile a4b3400
aws sts get-caller-identity --profile a4b3400
node scripts/configure-cognito.mjs --profile a4b3400 --expected-account YOUR_12_DIGIT_ACCOUNT_ID --origin http://localhost:3041
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

## 4. 사용자별 자료 분리 범위

회원 정보는 RDS에 저장한다. **문서·워크플로우 등 업무 데이터의 전체 PostgreSQL 이관은 이 변경에 포함되지 않는다.** 현재 Node/Python 업무 저장소를 `EASYFLOW_DATA_DIR/users/RDS_MEMBER_UUID/` 아래에 격리한다.

- Node 워크플로우·실행 이력·암호화된 연결 계정 및 보호 키
- Python 에이전트·대화·업로드 문서·문서 검토·실행 보고서
- 파서 결과·원본 파일·예약 실행·LLM 체크포인트

작업 공간은 인증된 회원 ID로만 선택한다. 요청의 userId/ownerId 및 다른 사용자의 자료 ID로 저장소를 바꾸지 않는다. Python 작업에 동일한 사용자 디렉터리를 전달하고, 예약 실행과 실행 큐도 작업 공간별로 분리한다. 재시작 시 기존 사용자 디렉터리의 예약을 다시 시작한다. 기존 공유 루트 자료를 새 계정으로 자동 이전하지 않는다.

외부 Webhook은 `/hooks/MEMBER_UUID/WORKFLOW_ID/PATH` 주소를 사용한다. 로그인 쿠키 대신 기존 Webhook 토큰·인증 설정을 검사한다. 비활성 회원의 Webhook은 거부한다. 인증 없음으로 설정한 Webhook은 해당 사용자의 워크플로우를 외부에서 호출할 수 있으므로 워크플로우의 의도에 맞게 설정한다.

로그아웃하면 서버 세션을 즉시 삭제하고 Refresh Token을 폐기한 뒤 Cognito 로그아웃 화면으로 이동한다. 복사한 세션 쿠키도 더 이상 사용할 수 없다. 관리자·회원 모두 자기 작업 공간에만 접근하며, 다른 회원 자료에 접근하는 관리자 UI는 추가하지 않는다.

로그아웃은 `/api/auth/logout`에 같은 출처의 JSON POST를 보낸 다음 반환된 Cognito 주소로 이동한다. `no-referrer` 정책에서 HTML 폼 POST의 Origin이 `null`이 되는 브라우저 동작에도 요청 출처 검사를 유지한다.

## 5. 배포와 검증

운영 도메인으로 CloudFormation의 PublicOrigin과 앱의 EASYFLOW_PUBLIC_URL을 함께 변경한다. HTTPS 프록시는 원래 Host를 전달해야 하며, 서버는 X-Forwarded-Host로 접속 출처를 변경하지 않는다. 변경 API와 로그아웃은 정확한 Origin을 요구한다. 인증 없이 조회 가능한 API는 `/api/auth/me`와 최소 상태만 반환하는 `/api/health`다.

서명된 테스트 JWT로 다음을 검증한다. 실제 Cognito 이메일 인증과 RDS 연결은 유효한 AWS 계정·리소스·DB 설정에서 별도로 완주해야 한다.

```bash
npm run check
npm run build
node --import tsx --test Backend/api/tests/cognito-auth.test.ts Backend/api/tests/oauth.test.ts Backend/api/tests/webhook-api.test.ts Frontend/tests/auth.test.ts Frontend/tests/frontend-dom.test.ts
```

두 사용자 간 워크플로우·연결 계정·실행/다운로드 접근 차단, 요청 출처 검사, 잘못된 토큰 거부, 콜백 재사용 방지, 동시 갱신, 로그아웃, 비활성 계정 접근 차단을 확인한다.

## 실제 RDS 연결 검증 — 2026-11-02

기존 비공개 RDS의 `a4b_app` 계정으로 TLS 1.3 연결과 인증서 검증을 확인했다. 실제 Cognito 인증 후 `a4b.users`에 회원이 생성되고 기본 `member` 권한으로 작업 화면에 진입했으며, 로그아웃 후 재로그인해도 같은 회원 UUID가 유지되고 중복 행이 생기지 않는 것을 확인했다.

Docker의 `host.docker.internal`을 통해 SSH 포워딩에 연결할 때도 실제 RDS 인증서의 DNS 이름을 검증한다. `pg`가 TLS 옵션의 SNI를 TCP 호스트로 다시 지정하므로, 명시한 DB 이름에 대한 검사는 Node의 기본 `checkServerIdentity`로 수행한다. 인증서 체인 검증을 끄지 않으며, 다른 호스트 인증서를 거부하는 회귀 테스트가 있다.

검증용 Cognito 계정과 임시 비밀번호·DB 연결 환경 파일을 삭제했다. 앱 DB 계정에는 회원 DELETE 권한이 없으므로 검증용 RDS 회원은 비활성화로 정리했다. 사용자의 승인 조건에 따라 기존 EC2와 RDS를 다시 중지했고, 두 리소스 모두 실제 `stopped` 상태를 확인했다. 관련 회귀 테스트 121개가 통과했다. 리소스 최종 상태와 비밀값 없는 검증 결과는 Git에서 제외된 `runtime/cognito-review/`에 기록한다.

RDS가 중지된 동안 `http://localhost:3041`의 확인용 앱은 `.env.cognito.local`의 명시적인 SQLite 개발용 회원 저장소를 사용한다. 운영 시에는 RDS 실행 상태와 네트워크를 준비하고 `postgres` 저장소를 사용해야 한다. 로컬 SQLite 업무 데이터의 전체 RDS 이관은 별도 작업이다.
