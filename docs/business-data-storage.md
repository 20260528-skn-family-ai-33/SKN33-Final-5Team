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

**연결 수.** Python 작업은 요청마다 새 프로세스로 뜨기 때문에, 연결 수는 풀 크기가 아니라 프로세스 수로 정해집니다. 서버 한 대의 최대 연결 수는 `(A4B_WORKER_MAX_CONCURRENCY + A4B_WORKER_CONTROL_CONCURRENCY) × 2 + Node 풀 8`입니다(회원 저장소 5 + 어시스턴트 체크포인트 3). 기본값으로는 40입니다. 서버가 여러 대면 모든 서버의 합이 RDS `max_connections`에서 관리용 여유분을 뺀 값보다 작아야 합니다.

**작업 공간 격리.** PostgreSQL에서는 모든 표가 `a4b_builder` 스키마에 있고 `workspace_id` 열을 가집니다. 값은 SQLite 폴더 이름(`data/users/<회원ID>`)이고, 로그인을 끈 로컬 실행에서는 `local`입니다. 기본 키·고유 키·외래 키·인덱스는 모두 `workspace_id`로 시작합니다. 앱의 모든 SQL이 `workspace_id`로 거르고, `FORCE ROW LEVEL SECURITY` 정책이 연결의 `a4b.workspace_id`와 다른 행은 읽기·쓰기 모두 막습니다. 그래서 앱 계정은 superuser나 `BYPASSRLS`가 아니어야 하고, 그런 계정이면 작업이 시작되지 않습니다. 같은 작업 공간의 쓰기는 SQLite `BEGIN IMMEDIATE`처럼 작업 공간 단위 advisory lock으로 순서를 맞춥니다. 그래서 revision 낙관적 잠금과 예약 작업 lease가 SQLite와 같게 동작합니다.

## 스키마 적용

앱은 PostgreSQL 표를 직접 만들지 않습니다. 스키마는 [infrastructure/db/migrations](../infrastructure/db/migrations)의 SQL 파일로 관리하고, 새 마이그레이션이 생길 때마다 관리자 계정으로 한 번 적용합니다. 앱은 트래픽을 받기 전에 앱 계정으로 접속해 스키마 버전, 행 수준 보안 우회 여부, 표 권한을 확인하고, 하나라도 맞지 않으면 시작하지 않습니다. 그래서 RDS에 접속할 수 없을 때 `/api/health`가 통과하는 일이 없습니다. `node Backend/api/build-server/index.mjs --preflight`는 이 확인만 하고 끝납니다. 앱 접속 계정은 [prerequisites.sql](../infrastructure/db/prerequisites.sql)의 `a4b_app` 역할 멤버여야 합니다.

```bash
# EC2: 스키마를 만들 수 있는 관리자 주소. 앱의 A4B_DATABASE_URL과 다른 계정이어야 합니다.
export A4B_MIGRATION_DATABASE_URL='postgresql://ADMIN:URL_ENCODED_PASSWORD@RDS_HOST:5432/a4b_db'
docker compose --env-file .env.aws run --rm -e A4B_MIGRATION_DATABASE_URL app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/apply-builder-migrations.py --dry-run
docker compose --env-file .env.aws run --rm -e A4B_MIGRATION_DATABASE_URL app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/apply-builder-migrations.py
```

`002`는 LangGraph `PostgresSaver.setup()`으로 체크포인트 표를 만든 뒤 작업 공간 열과 보안 정책을 더합니다. 이미 적용한 버전은 건너뛰므로 다시 실행해도 안전합니다.

## 기존 SQLite 자료 옮기기

[scripts/deploy/migrate-sqlite-to-postgres.py](../scripts/deploy/migrate-sqlite-to-postgres.py)는 일회용 이전 스크립트입니다. `data/users/*/`의 `agent-builder.sqlite`(업무 표·Python 체크포인트)와 `agent-checkpoints.sqlite`(Node 체크포인트)를 폴더 이름을 `workspace_id`로 넣어 복사합니다. 이미 있는 행은 건너뛰므로 두 번 실행해도 중복되지 않습니다. 부모가 없는 옛 행(예: 실행 기록이 지워진 검토 결정)은 건너뛰고 건수를 보여줍니다. SQLite 파일은 바꾸지 않습니다.

1. 앱을 멈추고 `a4b-data` 볼륨을 백업합니다.
2. `--dry-run`으로 작업 공간·표별 건수를 확인합니다. dry-run은 PostgreSQL에 접속하지 않습니다.
3. 위의 스키마 적용을 마친 뒤 실제로 복사합니다. 앱 계정(`A4B_DATABASE_URL`)으로 접속하고, 작업 공간마다 한 트랜잭션으로 넣습니다.
4. `A4B_STORAGE=postgres`를 적용하고 로그인해서 자료를 확인합니다.
   - `docker compose --env-file .env.aws`로 운영하면 `.env.aws`에 넣고 다시 시작합니다.
   - GitHub Actions EC2 자동 배포(`scripts/deploy/deploy-a4b-ec2.sh`)는 실행 중인 컨테이너의 환경을 복사합니다. 그래서 EC2의 `/var/lib/a4b-deploy/env-overrides`(root 소유, 권한 600, 한 줄에 `KEY=VALUE`)에 `A4B_STORAGE=postgres`를 넣은 뒤 배포를 실행합니다.

자동 배포는 기존 컨테이너를 멈추기 전에 새 이미지로 `--preflight`를 실행합니다. `A4B_STORAGE`가 없거나, 스키마·데이터 이전 전이거나, RDS에 접속할 수 없으면 배포를 멈추고 기존 컨테이너를 그대로 둡니다. 따라서 이 변경을 병합한 뒤 첫 배포 전에 위 1~4단계를 마쳐야 합니다.

```bash
docker compose --env-file .env.aws run --rm app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/migrate-sqlite-to-postgres.py --dry-run
docker compose --env-file .env.aws run --rm app /app/Backend/.venv-agent/bin/python /app/scripts/deploy/migrate-sqlite-to-postgres.py
```

로그인을 끈 루트 폴더(`local` 작업 공간)도 옮기려면 `--include-local`을 붙입니다. 로그인한 운영 서버는 `local` 작업 공간을 쓰지 않습니다.

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
