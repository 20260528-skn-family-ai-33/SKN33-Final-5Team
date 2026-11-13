# 업무 데이터 저장소 — SQLite · PostgreSQL

에이전트 빌더의 업무 데이터(설계 대화·자료·저장한 에이전트 버전·실행 기록·lease·예약 작업·검토 결정·Python LangGraph 체크포인트)와 Node 어시스턴트 체크포인트는 `A4B_STORAGE`로 저장소를 고릅니다. 로그인 회원 저장소(`A4B_AUTH_USER_STORE`)와 같은 방식입니다.

| 설정 | 기본값·용도 |
| --- | --- |
| `A4B_STORAGE` | `sqlite`(기본). 작업 공간 폴더마다 `agent-builder.sqlite`를 씁니다. `EASYFLOW_PUBLIC_URL`이 localhost가 아니면 시작을 거부합니다. 운영은 `postgres`입니다. |
| `A4B_DATABASE_URL`·`A4B_DATABASE_CA_FILE`·`A4B_DATABASE_TLS_SERVER_NAME` | `postgres`일 때 회원 저장소와 같은 값을 씁니다. TLS 규칙도 같아서 항상 인증서와 호스트 이름을 검증하고(`sslmode=verify-full`), 주소에 SSL 옵션을 넣으면 거부합니다. |
| `A4B_WORKER_MAX_CONCURRENCY` | `postgres`일 때 서버 한 대에서 동시에 띄우는 Python 작업 수입니다. 기본 12이고, 넘치면 대기열에서 기다립니다. 모델 호출과 실행처럼 오래 걸리는 작업은 이 중 약 3분의 2(기본 8)까지만 쓰므로, 예약 확인과 화면 요청이 막히지 않습니다. |
| `A4B_WORKER_CONTROL_CONCURRENCY` | 실행 소유권 갱신·중단(heartbeat·interrupt) 전용 상한입니다. 기본 4이고, 일반 작업 대기열에 막히지 않습니다. |
| `A4B_WORKER_QUEUE_TIMEOUT_MS` | 빈 자리를 기다리는 최대 시간입니다. 기본 60000ms이고, 넘기면 "처리 중인 작업이 많습니다" 오류를 냅니다. 예약 실행은 실패로 기록하지 않고, lease가 끝난 뒤(최대 5분) 다시 시도합니다. |
| `A4B_DATABASE_POOL_MAX` | Python 작업 하나가 역할(업무 데이터·체크포인트)마다 여는 연결 수입니다. 기본 1이라 작업 하나가 연결을 최대 2개 씁니다. |

**연결 수.** Python 작업은 요청마다 새 프로세스로 뜨기 때문에, 연결 수는 풀 크기가 아니라 프로세스 수로 정해집니다. 서버 한 대의 최대 연결 수는 `(A4B_WORKER_MAX_CONCURRENCY + A4B_WORKER_CONTROL_CONCURRENCY) × 2 + Node 풀 12`입니다(회원 저장소 5 + 어시스턴트 체크포인트 3 + 팀 관리 4). 기본값으로는 44입니다. 서버가 여러 대면 모든 서버의 합이 RDS `max_connections`에서 관리용 여유분을 뺀 값보다 작아야 합니다.

**작업 공간 격리.** `a4b_builder`의 회원별 업무·체크포인트 표에는 `workspace_id`가 있습니다. 값은 `data/users/<회원ID>`의 회원 ID이고, 로그인을 끈 로컬 실행에서는 `local`입니다. 행 수준 보안 정책은 현재 연결의 작업 공간에 속하는 행만 읽고 쓰도록 제한합니다. 앱 계정은 superuser나 `BYPASSRLS` 권한으로 접속할 수 없습니다. 스키마 버전과 볼륨 이전 완료 기록은 DB 단위로 관리합니다.

## 자료함과 개인 폴더

자료함은 설계 대화 없이 파일을 등록하고, 개인 폴더로 정리하는 화면입니다. 기존 설계·실행에서 올린 자료도 처음에는 개인 자료함의 최상위에 보입니다. 폴더 이동·이름 변경은 정리 정보만 바꾸므로 자료 ID, 원본 저장 경로, 저장된 워크플로우와 실행 기록의 참조는 유지됩니다.

정리 정보는 `library_spaces`, `library_folders`, `library_asset_placements`에 저장합니다. 개인 자료 공간은 서버가 현재 회원의 작업 공간에서 결정합니다. 팀 공간은 인증 회원의 구성원 권한을 확인한 후 `data/teams/<팀 UUID>`와 `workspace_id=team:<팀 UUID>`로 선택합니다. 클라이언트는 저장소 소유자를 지정할 수 없습니다.

자료함에서는 현재 폴더의 파일 검색, 하위 폴더 생성, 폴더 이름 변경·이동, 파일 이동, 기존 내용 보기와 자료 사용처 확인을 제공합니다. 원본 파일 등록은 기존 문서·표 파서와 파일당 8MB 제한을 재사용합니다. 파일이 있거나 하위 폴더가 있는 폴더는 삭제할 수 없습니다. 폴더 이름 중복과 자기 자신·하위 폴더로의 이동도 서버에서 차단합니다.

PostgreSQL 배포에는 `005_personal_asset_library.sql`부터 최신 `008_workflow_folders.sql`까지의 적용이 필요합니다. 현재 Node와 Python의 필수 스키마 버전은 8입니다. 운영 배포 전에 새 코드/검증 이미지의 `scripts/deploy/apply-builder-migrations.py --dry-run`으로 미적용 항목을 확인하고 관리자 계정으로 적용한 다음, 같은 이미지의 앱 계정 시작 전 검증을 통과해야 합니다. 기존 RDS에는 신규 빈 DB용 `prerequisites.sql`·`schema.sql`을 다시 적용하지 않습니다. 로컬 SQLite는 자료함 표를 추가하며 기존 자료를 새 파일로 복사하지 않습니다.

## 팀 공유와 서비스 내부 초대

팀을 만든 회원은 자동으로 관리자가 됩니다. 관리자는 이메일과 관리자·편집자·열람자 역할로 초대를 등록하고, 이메일 인증을 마친 동일 주소의 회원이 자료함의 받은 초대에서 수락·거절합니다. 가입 전 주소도 초대할 수 있으며 외부 이메일은 발송하지 않습니다. 초대는 7일 후 만료되고 관리자가 취소할 수 있습니다. 마지막 관리자의 강등·제외는 서버 트랜잭션에서 차단합니다.

관리자와 편집자는 팀 파일·폴더를 변경하고, 열람자는 조회·원본 다운로드만 할 수 있습니다. 팀원·초대 관리는 관리자 권한이 필요합니다. 모든 팀 요청과 대기열에서 워커를 시작하는 시점에 구성원 권한을 재확인합니다. 팀 경로는 자료함 API만 허용하며 개인의 Gmail·워크플로우 실행·스케줄러를 노출하지 않습니다.

중앙 구성원 정보는 운영에서 `a4b_builder.teams`, `team_members`, `team_invitations`에 저장하며 트랜잭션의 회원·이메일·인가된 팀 컨텍스트를 사용하는 FORCE RLS를 적용합니다. 로컬 로그인 개발에서는 루트 `teams.sqlite`를 사용합니다. 이 중앙 SQLite에 실제 팀 데이터가 있으면 현재 SQLite→PostgreSQL 이전 도구는 복사를 중단합니다. 구성원 메타데이터의 별도 이전 절차가 준비될 때까지 원본 `teams.sqlite`와 `teams/`를 함께 보존해야 합니다.

워크플로우에서 팀 파일을 선택하면 현재 원본을 개인 자료함에 독립 사본으로 복사해 연결합니다. 팀 파일 변경·삭제·퇴출은 이미 내려받거나 복사한 개인 자료를 회수하지 않습니다. 사본은 원본을 다시 분석하므로 팀 원본에 수동으로 적용한 문서 검토 수정은 복사되지 않습니다. 팀 자료는 회원 UUID가 아니므로 기존 `a4b.documents` 등의 개인 업무 기록에 이중 저장하지 않습니다.

S3를 쓰는 배포는 기존 `app/v1/users/*` 외에 `app/v1/teams/*/builder-originals/*`의 GetObject·PutObject·DeleteObject 권한을 확인해야 합니다. 원본은 각 개인/팀 prefix에 별도로 저장합니다. 로그인 비활성 환경에서는 개인 자료함만 사용할 수 있습니다.

## RDS에 기록되는 데이터

기존 운영 업무 표에 새 기록을 함께 남기는 설정은 `A4B_STORAGE=postgres`, `A4B_BUSINESS_RECORDS=a4b`입니다.
Cognito와 PostgreSQL 회원 저장소를 쓰는 환경에서 **새로 시작하는** 빌더에 적용합니다.
이미 자료가 있는 PostgreSQL 빌더에 설정만 추가하는 전환은 지원하지 않습니다.
회원의 업무 데이터와 내부 상태는 같은 PostgreSQL 트랜잭션에서 저장합니다. LangGraph는 각 단계가 끝날 때 별도로 체크포인트를 저장합니다.

| 앱에서 한 일 | 기존 `a4b`에서 볼 곳 | 내부 기록과 연결하는 값 |
| --- | --- | --- |
| 대화 시작·질문·응답 | `design_sessions.messages_json`, `workflows` | 대화 ID = `a4b_builder.checkpoints.thread_id` |
| 생성 요청·질문 대기·생성 차단·오류 | `a4b.workflow_generation_requests` | `id` = 생성 요청 ID, `session_id` = 설계 대화 ID |
| 설정 저장 | `workflow_versions`의 버전·그래프 | `workflow_id` = 빌더 에이전트 ID |
| 자료 첨부 | `documents`, S3 사용 시 `document_versions`의 원본 위치·해시·바이트 수 | 문서 ID = 빌더 자료 ID |
| 실행·실패·재시도 | `workflow_runs`, `node_runs`, 성공 결과는 `outputs` | `state_json->>'builder_run_id'` = 빌더 실행 ID. `state_json->>'checkpoint_thread_id'`로 재개 지점을 조회 |

실패한 실행을 재시도하면 이전 실패 행은 보존하고 새 실행 행을 만듭니다. 원래 빌더 실행 ID로 두 행을 함께 찾을 수 있습니다.
저장된 실행의 마지막 상태와 실패 단계를 확인하는 예입니다. `:builder_run_id`에 앱의 실행 ID를 입력합니다.

```sql
SELECT r.id, r.status, r.state_json->>'error' AS error,
       n.node_key, n.status AS node_status, n.error_json
FROM a4b.workflow_runs r
LEFT JOIN a4b.node_runs n ON n.run_id = r.id
WHERE r.state_json->>'builder_run_id' = :builder_run_id
ORDER BY r.created_at, r.started_at, n.started_at;
```

### 워크플로우가 만들어지지 않은 이유 조회

`message`(업무 설명·추가 답변)와 `confirm`(저장 확인)마다 새 생성 요청 ID를 발급합니다. 응답의 `generation_request_id`로 해당 요청을 찾습니다. 같은 대화의 후속 답변과 재시도는 이전 요청의 이력을 덮어쓰지 않습니다. 이 기록은 실행 이력인 `workflow_runs`와 별개이며, `A4B_STORAGE=postgres`이면 `A4B_BUSINESS_RECORDS` 설정과 관계없이 RDS에 저장됩니다.

`a4b.workflow_generation_requests`의 `workspace_id`는 회원 ID, `id`는 요청 ID, `session_id`는 대화 ID입니다. `json`에는 요청 본문(최대 4,000자, 알려진 비밀값 마스킹), 요청 당시 대화 revision, 단계·세부 노드, 상태, 사유 코드·설명, 예외 유형, 시작·갱신·종료 시각, 저장한 워크플로우 ID, 단계별 `events`가 있습니다. 모델 호출 직전에 단계를 커밋하므로 워커 제한 시간 초과도 마지막 단계에 연결합니다. 워크플로우 저장과 `succeeded` 기록은 같은 트랜잭션으로 커밋합니다. 로컬 SQLite에서는 기존과 같이 `builder_generation_requests`를 사용하고, PostgreSQL 이전 시 `a4b.workflow_generation_requests`로 복사합니다.

| 필드 | 값과 의미 |
| --- | --- |
| `stage` | `request_analysis` 요청 분석 · `design` 설계 · `validation` 검증 · `save` 저장 |
| `status` | `running` 처리 중 · `waiting_input` 추가 질문 대기 · `waiting_confirmation` 저장 확인 대기 · `blocked` 도구·검증 문제로 생성 차단 · `failed` 시스템·모델·저장 오류 · `succeeded` 저장 완료 |
| `reason_code` 예 | `model_timeout`, `invalid_model_response`, `model_connection_failed`, `unsupported_tools`, `verification_failed`, `save_failed`, `worker_timeout`, `worker_busy` |

`waiting_input`과 `waiting_confirmation`은 오류가 아닙니다. `finished_at`은 해당 HTTP 생성 요청의 처리가 끝난 시각이며 대화 자체의 종료 시각은 아닙니다. 참고용 검증(`shadow`)의 오류는 `events`에 경고로 남기고, 실제 생성 차단 사유로 분류하지 않습니다. 검증 강제(`enforce`)가 생성·저장을 막으면 최종 상태와 사유에도 기록합니다. 원시 모델 응답·제공자 오류 본문·연결 계정 키는 이력에 저장하지 않습니다.

다음 조회는 관리자 등 조회 권한이 있는 계정에서 실행합니다. 앱 계정으로 직접 조회할 때는 연결의 `a4b.workspace_id`를 해당 회원 ID로 설정해야 합니다. 행 수준 보안으로 다른 회원의 이력은 보이지 않습니다.

```sql
SELECT workspace_id, id AS request_id, session_id,
       json->>'request_text' AS request_text,
       json->>'stage' AS stage, json->>'node' AS node,
       json->>'status' AS status, json->>'reason_code' AS reason_code,
       json->>'reason' AS reason, json->>'error_type' AS error_type,
       json->>'started_at' AS started_at, json->>'finished_at' AS finished_at
FROM a4b.workflow_generation_requests
WHERE session_id = :session_id
ORDER BY seq DESC;
```

앱에서도 `GET /api/agent-builder/generation_history?id=<대화 ID>`로 현재 회원의 해당 대화에 대한 최근 50건과 단계별 이력을 조회할 수 있습니다. DB에는 50건 이전 기록도 보존합니다. 기존 생성 요청의 이력을 소급 생성하지 않으며, 마이그레이션 `004_generation_requests.sql` 적용과 새 서버 배포 이후 수락한 요청부터 기록합니다. 잘못된 HTTP 형식·인증 실패처럼 생성 요청을 수락하기 전의 거절은 포함하지 않습니다. DB 자체에 접속할 수 없으면 DB에 실패 이력을 기록할 수 없고 요청도 실패합니다. 서버 전체가 비정상 종료된 경우 마지막으로 커밋된 `running` 단계가 남을 수 있으며, 이를 성공이나 질문 대기로 추정하지 않습니다.

| 업무 | 저장 위치와 내용 |
| --- | --- |
| 로그인 | 기존 `a4b.users`에 Cognito 제공자·사용자 식별자, 이메일, 이름을 등록하거나 갱신합니다. 회원 ID, 역할, 활성 상태도 여기서 읽습니다. |
| 설계와 자료 | `builder_sessions`에 대화 상태와 수정 버전, `builder_assets`에 파일 정보·추출한 내용·표 행, `agent_versions`에 저장한 에이전트 설정을 보관합니다. |
| 실행과 예약 | `agent_tests`에 실행 상태·출력, `builder_run_leases`에 실행 소유권·만료 시각, `builder_data_sources`, `builder_schedules`, `builder_schedule_jobs`에 자료 연결·예약·처리 상태를 기록합니다. |
| 검토와 도구 | 문서 검토와 수정 이력, 응대 승인·거절, 지연 검증 입력·결과, 도구 목록, 재사용 노드, 도구 제작 요청, 전처리 배치·예시를 보관합니다. |
| 대화·실행 재개 | Python은 `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`에, Node 어시스턴트는 `agent_checkpoints`에 중단 지점과 재개할 상태를 저장합니다. |
| 운영 기록 | `schema_migrations`와 `checkpoint_migrations`에는 적용 버전이, `sqlite_imports`에는 이전 식별자·작업 공간 수·완료 시간이 남습니다. |

별도 표시가 없는 표는 `a4b_builder` 스키마에 있습니다. 업로드 원본과 결과 파일의 바이트는 기존 파일 저장 경로(설정에 따라 S3 또는 데이터 볼륨)를 사용합니다. 추출된 표 행과 본문, 실행 결과 JSON은 RDS에도 들어갈 수 있습니다. 일반 Node 워크플로와 암호화된 연결 계정은 계속 볼륨의 `easyflow.sqlite`에 저장합니다.

## 내 자료

왼쪽 **내 자료**는 현재 작업 공간의 `builder_assets`에서 설계 첨부, 실행 첨부, 예약 데이터 소스 업로드를 조회합니다. 원본을 열 때는 서버가 자료 ID의 소유 작업 공간을 확인합니다. 로컬에서는 데이터 볼륨을 사용하고, `A4B_S3_BUCKET`이 설정된 환경에서는 회원별 S3 경로에서 읽습니다.

파일 옆에 연결된 설계, 저장된 워크플로우, 실행 기록, 예약 데이터 소스의 이름을 표시합니다. 설계 화면의 **자료 연결 → 내 자료에서 선택**을 누르면 이미 올린 자료를 다른 설계에도 연결할 수 있습니다. 두 설계는 같은 자료 ID와 검토 결과를 사용하며 S3에 원본을 다시 쓰지 않습니다.

저장된 워크플로우나 과거 실행 기록에 연결한 참고 파일도 삭제할 수 있습니다. 삭제하면 설계 대화에서 연결이 풀리고, 서버는 자료의 추출 내용과 로컬 원본·분석 파일을 정리합니다. S3를 사용하면 원본 객체도 삭제합니다. 기존 워크플로우 버전과 실행 결과는 보관합니다. 저장된 버전이 참조하는 입력은 파일 이름·종류·열 이름을 보관해 다음 실행에서 새 파일을 연결할 수 있습니다. 실행 중에 사용하는 파일은 실행이 끝난 후, 예약 데이터 소스는 다른 파일로 교체한 후 삭제할 수 있습니다.

운영 서버 역할에는 해당 버킷의 `app/v1/users/*/builder-originals/*` 객체에 대한 `s3:DeleteObject` 권한이 필요합니다. 정리 도중 실패하면 **내 자료**에서 삭제를 재시도할 수 있습니다.

## 스키마 적용

앱은 PostgreSQL 표를 직접 만들지 않습니다. 스키마는 [infrastructure/db/migrations](../infrastructure/db/migrations)의 SQL 파일로 관리하고, 새 마이그레이션이 생길 때마다 관리자 계정으로 한 번 적용합니다. 앱은 트래픽을 받기 전에 앱 계정으로 접속해 스키마 버전, 행 수준 보안 우회 여부, 표 권한을 확인하고, 하나라도 맞지 않으면 시작하지 않습니다. 그래서 RDS에 접속할 수 없을 때 `/api/health`가 통과하는 일이 없습니다. `node Backend/api/build-server/index.mjs --preflight`는 이 확인만 하고 끝납니다. 앱 접속 계정은 [prerequisites.sql](../infrastructure/db/prerequisites.sql)의 `a4b_app` 역할 멤버여야 합니다.

```bash
# EC2: 스키마를 만들 수 있는 관리자 주소. 앱의 A4B_DATABASE_URL과 다른 계정이어야 합니다.
export A4B_MIGRATION_DATABASE_URL='postgresql://ADMIN:URL_ENCODED_PASSWORD@RDS_HOST:5432/a4b_db'
docker compose --env-file .env.aws run --rm -e A4B_MIGRATION_DATABASE_URL app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/apply-builder-migrations.py --dry-run
docker compose --env-file .env.aws run --rm -e A4B_MIGRATION_DATABASE_URL app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/apply-builder-migrations.py
```

`002`는 LangGraph 체크포인트 표에 작업 공간 열과 보안 정책을 더하고, `003`은 볼륨 이전 완료 기록, `004`는 `a4b.workflow_generation_requests`와 행 수준 보안, `005`는 자료함 공간·폴더·파일 배치 정보, `006`은 팀·구성원·서비스 내부 초대를 만들며, `007`은 예약 자료 연결, `008`은 워크플로우 폴더를 만듭니다. 적용 버전은 계속 `a4b_builder.schema_migrations`에서 관리하며 이미 적용한 버전은 건너뜁니다. 자동 배포에서 증분 마이그레이션을 적용하려면 EC2의 `/var/lib/a4b-deploy/migration-env`를 root 소유·권한 `0600`의 일반 파일로 준비하고 `A4B_MIGRATION_DATABASE_URL=postgresql://...` 한 줄만 넣습니다. 배포 스크립트는 새 이미지의 사전 검사 전에 관리자 URL을 일회성 마이그레이션 컨테이너에만 전달합니다. 이 URL을 `env-overrides`나 앱 컨테이너 환경에 넣지 않습니다. 파일이 없고 필요한 버전이 미적용이면 사전 검사가 실패하며 기존 앱을 유지합니다.

## 기존 SQLite 자료 옮기기

[scripts/deploy/migrate-sqlite-to-postgres.py](../scripts/deploy/migrate-sqlite-to-postgres.py)는 `data/users/*/`의 `agent-builder.sqlite`(업무 표·Python 체크포인트)와 `agent-checkpoints.sqlite`(Node 체크포인트)를 회원 ID별로 복사합니다. 대상에 같은 키의 업무 행이나 Node 체크포인트가 있으면 원본과 내용이 같을 때만 건너뜁니다. 다르면 이전을 중단합니다. 부모가 사라진 과거 검토 결정 등의 행은 dry-run에 제외 건수가 표시됩니다. 원본 SQLite 파일은 읽기 전용으로 엽니다.

1. SQLite에 쓰는 앱과 워커를 정지하고 실제 데이터 볼륨과 대상 DB를 백업합니다. EC2 자동 배포를 사용하면 [EC2 전환 절차](a4b-automatic-deployment.md#업무-데이터-저장소-전환)를 따릅니다.
2. `--dry-run`으로 작업 공간·표별 건수를 확인합니다. dry-run은 PostgreSQL에 접속하지 않습니다.
3. 스키마를 적용한 뒤 앱 계정(`A4B_DATABASE_URL`)으로 실제 복사를 실행합니다. 업무 행과 Node 체크포인트는 작업 공간별로, Python 체크포인트와 보류 쓰기는 체크포인트별로 커밋합니다. 실패 시 원본을 정지한 채 같은 명령을 다시 실행합니다.
4. `A4B_STORAGE=postgres`를 적용하고 로그인해서 자료를 확인합니다.
   - `docker compose --env-file .env.aws`로 운영하면 `.env.aws`에 넣고 다시 시작합니다.
   - EC2 자동 배포는 정지한 기존 컨테이너의 환경과 마운트를 인계받습니다. `/var/lib/a4b-deploy/env-overrides`(root 소유, 권한 600)에 `A4B_STORAGE=postgres`를 추가하고 배포합니다.

모든 복사가 끝나면 스크립트가 대상 DB의 `sqlite_imports`에 기록을 커밋한 뒤 같은 식별자를 원본 볼륨의 `.a4b-sqlite-import`에 게시합니다. 서버 기동과 `--preflight`에서 둘의 일치를 확인합니다. dry-run, 부분 실패, 다른 볼륨이나 DB로는 이 검사를 통과할 수 없습니다. 백업·복원 시 완료 파일도 포함하세요.

```bash
docker compose --env-file .env.aws run --rm app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/migrate-sqlite-to-postgres.py --dry-run
docker compose --env-file .env.aws run --rm app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/migrate-sqlite-to-postgres.py
```

루트에 기존 SQLite 업무 DB가 있으면 dry-run과 실제 이전 모두에 `--include-local`을 붙여 `local` 작업 공간까지 옮깁니다. 회원 ID를 알 수 없는 폴더에 업무 DB가 있으면 이전을 중단합니다. 신규 설치에서 dry-run으로 실제 마운트와 빈 원본을 확인한 경우에만 실제 이전에 `--confirm-empty`를 붙입니다. 원본 DB 파일이 하나라도 있으면 이 옵션은 거부됩니다.

## PostgreSQL 저장소 테스트

기본 테스트는 SQLite로 돕니다. `A4B_TEST_DATABASE_URL`이 있으면 [test_storage_backends.py](../Backend/tests/infrastructure/test_storage_backends.py)의 같은 저장소 테스트를 PostgreSQL로도 돌립니다. 이 테스트는 두 작업 공간 사이의 조회·수정·예약 claim·체크포인트 격리와 보안 정책을 확인합니다. 지정한 DB의 `a4b_builder` 스키마를 지우고 다시 만들기 때문에, 이름에 `test`가 들어간 일회용 DB만 받습니다.

```bash
docker compose --profile test-db up -d --wait postgres-test
cd Backend && A4B_TEST_DATABASE_URL=postgresql://postgres:a4b-test@127.0.0.1:55432/a4b_test .venv-agent/bin/python -m pytest tests/infrastructure/test_storage_backends.py -q && cd ..
A4B_TEST_DATABASE_URL=postgresql://postgres:a4b-test@127.0.0.1:55432/a4b_test node --import tsx --test Backend/api/tests/storage-config.test.ts
docker compose --profile test-db down
```

## 알아둘 점

- JSON 원문 열은 `jsonb`가 아니라 `json` 타입입니다. `jsonb`는 객체 키 순서를 바꾸는데, 표의 열 순서(`export_result`)와 검증 지문이 저장된 키 순서에 의존하기 때문입니다. `->>` 조회는 `json`에서도 됩니다.
- 실행 lease의 프로세스 생존 확인(`pid`)은 같은 서버를 기준으로 합니다. 서버 여러 대가 같은 작업 공간을 실행하려면 이 부분을 먼저 보완해야 합니다.
- Node 워크플로 엔진의 `easyflow.sqlite`(워크플로·연결 계정)와 S3 파일 저장 방식은 바뀌지 않았습니다.
- `scripts/run/run-deferred-verifications.py`는 `data/users/*/` 회원 작업 공간을 모두 처리합니다. PostgreSQL에서는 행 수준 보안 때문에 다른 작업 공간을 조회할 수 없어 회원 폴더 이름으로 대상을 정하므로, 회원 폴더가 있으면 `local` 작업 공간은 처리하지 않습니다.
- `postgres`에서 `data/users/` 아래 폴더 이름이 회원 ID(UUID) 형식이 아니면 `local` 작업 공간에 합치지 않고 오류를 냅니다.
