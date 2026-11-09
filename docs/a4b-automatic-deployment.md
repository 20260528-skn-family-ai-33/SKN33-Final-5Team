# A4B EC2 자동 배포

`main`에 PR이 병합되거나 커밋이 푸시되면 기존 EC2에 자동으로 배포합니다. 병합 전에 작업 브랜치를 확인하려면 먼저 해당 브랜치를 이 저장소에 푸시한 뒤 **Actions → Deploy A4B to EC2 → Run workflow**를 엽니다. **Use workflow from**은 `main`으로 두고, `branch` 입력란에 테스트할 브랜치 이름을 넣습니다.

테스트 브랜치도 같은 EC2와 공개 HTTPS 주소에 배포됩니다. 팀원 모두 마지막으로 배포에 성공한 버전을 보게 됩니다. 테스트 직전 버전으로 빠르게 돌아가려면 `operation`에서 `restore`를 선택합니다. 테스트 시작 전 컨테이너를 보관해 두었다가 다시 실행하고 헬스 체크합니다. 최신 `main`을 올리려면 `operation=deploy`, `branch=main`으로 실행합니다. 테스트 중에 새 `main` 배포가 성공해도 운영 버전은 `main`으로 바뀝니다. 컨테이너를 교체하는 동안 접속이 잠시 끊길 수 있고, 테스트 중 운영 데이터에 생긴 변경은 복귀 후에도 남습니다.

새 커밋은 AWS 권한이 없는 작업에서 테스트합니다. 테스트를 통과하면 ECR 게시 전용 작업이 선택한 영역의 이미지를 빌드하고 ECR에 바로 올립니다. BuildKit은 변경되지 않은 레이어를 재사용합니다. ECR에 같은 커밋 SHA와 영역의 검증 이미지가 있으면 바로 재사용합니다. 운영 배포 작업은 이미지 태그를 받아 `main`의 스크립트로 EC2에 반영합니다.

GitHub에 `production` 환경을 만들고 워크플로우 실행 브랜치를 `main`으로 제한합니다. 운영 권한으로 실행해도 되는 팀 작업 브랜치만 선택합니다. 자동 배포와 수동 배포 모두 아래 변수를 사용하고, 같은 서버에서 동시에 실행되지 않도록 직렬화합니다.

| 변수 | 값 |
| --- | --- |
| `A4B_DEPLOY_ROLE_ARN` | GitHub Actions가 맡을 AWS 배포 역할 ARN |
| `A4B_ECR_REPOSITORY` | 서울 리전의 A4B ECR 저장소 URI. 이미지 태그는 붙이지 않습니다. |
| `A4B_EC2_INSTANCE_ID` | 기존 A4B EC2 인스턴스 ID |
| `A4B_PUBLIC_URL` | 기존 공개 HTTPS 주소. 끝에 슬래시를 붙이지 않습니다. |

두 GitHub 환경은 `main`만 허용합니다. `image-publish`에는 `A4B_IMAGE_PUBLISH_ROLE_ARN`과 `A4B_ECR_REPOSITORY`를 설정합니다. 이 환경의 OIDC 역할은 지정한 ECR 저장소에 이미지를 게시할 수 있습니다. `production`의 OIDC 역할은 ECR 이미지 조회와 지정한 EC2의 SSM 명령 실행 및 결과 조회를 담당합니다. EC2는 인스턴스 역할로 ECR에서 이미지를 내려받습니다.

교체 스크립트는 기존 `a4b-jhd-aws`의 환경 변수와 두 마운트를 재사용합니다. 따라서 테스트 중에도 기존 Cognito·RDS·S3 설정과 데이터에 연결됩니다.

새 이미지가 시작된 뒤 EC2의 앱 화면과 공개 `/api/health`를 확인하고, 실패하면 이전 컨테이너를 다시 시작합니다. ECR에서 새 이미지를 받으면서 이전 버전도 보관할 디스크 공간을 확인한 뒤 첫 배포를 실행합니다.

### RunPod 모델 설정 선택

기본 [AWS 환경변수 예시](../infrastructure/env/.env.aws.example)는
[RunPod 기본 설치](../scripts/runpod/README.md)의 `scripts/start-runpod-qwen-parser.sh`와 맞춥니다.
이 설치는 `qwen3.5:4b`와 `a4b-qwen35-4b-16k`를 준비합니다. 설계와 지원 판정은
`a4b-qwen35-4b-16k`를 공유하며, `A4B_CAPABILITY_MODEL`을 비워 두면 설계 모델을 사용합니다.
native 문맥은 16,384이고 `/v1` 모델의 Modelfile에도 같은 문맥이 등록됩니다.

#### 9B 모델 설정

[9B 추가 설정 예시](../infrastructure/env/.env.aws.qwen35-9b.example)는 별도로 준비된
최종664·기존105 파인튜닝 모델을 사용하는 경우에만 적용합니다. 기본 시작 스크립트는
이 두 모델을 내려받거나 등록하지 않으며, 비공개 어댑터와 GGUF는 이 저장소에 포함되지 않습니다.

1. 전달받은 모델 패키지의 `python serving/verify.py`로 무결성을 확인하고,
   해당 패키지의 `README_KO.md`에 따라 고정 원본·어댑터 병합, GGUF 변환·검증·등록을 완료합니다.
   원본 9B나 4B의 이름만 바꾸어 파인튜닝 모델로 사용하지 않습니다.
2. 사용할 Ollama 서버에 `a4b-qwen35-9b-final664-pilot32k`,
   `a4b-qwen35-9b-previous-pilot32k`, `qwen3.5:4b`가 등록되어 있어야 합니다.
   기존 데이터가 있다면 아래 **기존 4B 대화·워크플로우 전환**도 적용해야 합니다.
   두 9B 모델의 Modelfile에서 `PARAMETER num_ctx 32768`을 확인합니다.
   `A4B_OLLAMA_NUM_CTX=32768`은 native 요청 설정이며, `/v1` 모델의 등록 설정을 변경하지 않습니다.
3. 각 모델에 실제 요청을 보내 응답과 GPU 로딩·문맥 길이를 확인하고, GPU 파서도 별도로 검증합니다.
   화면의 AI 상태 검사는 모델 목록과 이름을 확인하므로 추론 성공이나 메모리 여유를 보장하지 않습니다.
4. 기본 예시를 `.env.aws`로 복사해 AWS 설정·주소·인증 값을 채우고, 9B 예시를
   `.env.aws.qwen35-9b`로 복사합니다. 두 파일의 접근 권한을 제한하고 아래 순서로 적용합니다.

   ```bash
   docker compose --env-file .env.aws --env-file .env.aws.qwen35-9b config --quiet
   docker compose --env-file .env.aws --env-file .env.aws.qwen35-9b up -d --build
   ```

두 번째 파일은 모델 설정만 덮어쓰므로 첫 번째 파일의 Cognito·RDS·S3·접속 주소와 인증 값을 유지합니다.
GitHub Actions의 기존 EC2 배포는 이 예시 파일을 자동으로 읽지 않고 현재 컨테이너 설정을 재사용합니다.
따라서 이미 운영 중인 9B 서버를 기본 4B 예시로 다시 설정할 필요가 없습니다.
모델 연결 화면까지 바뀐 버전을 반영할 때는 `target=all`로 프런트엔드와 백엔드를 함께 배포한 뒤,
EC2에서 모델 호출·파서 연결을 다시 확인합니다.

#### 기존 4B 대화·워크플로우 전환

`A4B_MAIN_MODEL`은 새 대화의 기본 모델입니다. 기존 대화, 저장 워크플로우 버전,
예약 작업의 고정본, 중단·실패한 실행은 저장 당시 모델명을 유지합니다.
따라서 위의 모델 3개만 준비하고 주소와 기본 모델을 바꾸면 상태 화면은 정상이어도
기존 `a4b-qwen35-4b-16k` 요청은 404로 실패할 수 있습니다.
`qwen3.5:4b`는 별도 이름이며 이 요청을 대신 받지 않습니다.

9B 추가 설정에는 다음의 **명시적 호출 전환**이 포함됩니다.

```dotenv
A4B_OLLAMA_MODEL_ALIASES='{"a4b-qwen35-4b-16k":"a4b-qwen35-9b-final664-pilot32k"}'
```

- native `/api/chat`와 OpenAI 호환 `/v1`에서 해당 이전 이름의 요청만 최종664로 보냅니다.
  `a4b-qwen35-4b-16k:latest`도 같은 이름으로 처리합니다. 다른 태그는 별도 항목이 필요합니다.
- 세션·워크플로우·고정본·체크포인트의 원래 모델 참조를 덮어쓰지 않습니다.
  기존 실행의 재개·재시도와 예약 고정본에도 적용되며, 새 9B 대화에서 기존 워크플로우를
  재사용할 때도 같은 로컬 연결과 실제 대상 모델인지 확인합니다.
- 이는 실제로 **다른 9B 가중치를 호출하는 전환**이므로 결과가 기존 4B와 같음을 보장하지 않습니다.
  과거 기록의 `model`은 원래 참조이며 현재 호출 대상은 이 설정과 함께 해석해야 합니다.
- 별도 OpenAI 연결 계정과 vLLM에는 적용하지 않습니다. 기본 4B 예시에서는 비어 있어
  기존 동작을 유지합니다. 미등록 모델을 임의로 현재 모델에 대체하는 자동 fallback은 없습니다.
- 키와 값은 모델명 문자열만 허용하며, `:latest` 중복·연쇄·순환 연결은 거부합니다.
  상태 검사는 별칭 대상 모델도 확인하지만 **DB 안의 모든 모델 참조나 실제 추론을 검사하지는 않습니다.**

전환 순서:

1. 기존 대화, 워크플로우 버전과 하위 워크플로우 고정본, 예약 대기 작업,
   재개·재시도할 실행에서 사용하는 로컬 모델명을 조사합니다. 위 예시에 없는 이름은
   검토 후 전환 설정에 명시하거나, 새 서버에 **기존 모델과 설정을 그대로** 등록해 유지합니다.
   원래 동작 보존이 필요하면 해당 이름을 별칭에서 빼고 기존 모델을 유지합니다.
2. 진행 중인 작업을 마치거나 안전하게 중단하고, 현재 앱 설정과 데이터 백업을 보관합니다.
   등록·추론 확인을 마친 9B 서버에 연결하며 위 전환 설정을 앱에 전달합니다.
   Compose는 앞서 안내한 두 환경 파일을 함께 사용합니다.
3. 기존 EC2의 Actions 배포는 예시 파일을 자동 적용하지 않습니다. EC2의
   `/var/lib/a4b-deploy/env-overrides`에 위 **변수명과 JSON 값**을 한 줄로 반영한 뒤 배포합니다.
   이 파일은 Docker `--env-file` 형식이므로 JSON 바깥의 작은따옴표를 넣지 않습니다:

   ```text
   A4B_OLLAMA_MODEL_ALIASES={"a4b-qwen35-4b-16k":"a4b-qwen35-9b-final664-pilot32k"}
   ```

   기존 파일의 다른 설정을 보존하고 root 소유·권한 600을 유지합니다.
   기본 모델·지원 판정 모델·주소·인증 값은 해당 서버에 준비한 9B 설정과 일치해야 합니다.
4. 실제 배포에서 **기존 4B 대화 이어가기 → 기존 AI 워크플로우 실행 → 중단 실행 재개와
   실패 실행 재시도 → 예약 작업 → 새 9B 대화에서 기존 워크플로우 재사용**을 확인합니다.
   Ollama 요청의 실제 모델명과 결과를 확인하고, 원래 모델 참조·고정 버전·완료된 단계가
   보존되는지 검사합니다. 새 대화·모델별 추론·파서 검사도 수행합니다.
5. 되돌릴 때는 먼저 참조 중인 기존 모델을 서버에 복원·검증합니다. 진행 중인 작업을 정리한 뒤
   전환 JSON에서 해당 항목을 제거하거나 빈 값으로 바꾸고 앱을 다시 배포합니다.
   별칭만 제거하면 기존 모델이 없는 서버에서는 다시 404가 납니다.

이 PR의 전환 회귀 검사는 임시 DB, 실제 워커·워크플로우 실행기와 모의 Ollama HTTP 응답을
사용합니다. 운영 DB의 참조 조사와 실제 RunPod 추론·예약 검증은 배포 단계에서 별도로 수행합니다.

### 배포 영역

워크플로우는 `Use workflow from`을 `main`으로 두고, `branch`에 테스트할 브랜치를 입력합니다.
`target=auto`는 EC2에 올라간 프론트·백엔드 각각의 버전과 대상 커밋을 비교합니다. 두 영역이 바뀌거나 공용 코드가 바뀌면 `all`로 배포합니다.

- `frontend`는 정적 파일만 빌드해 교체합니다. 실행 중인 백엔드 컨테이너를 재시작하지 않습니다.
- `backend`는 현재 프론트 파일을 새 백엔드 컨테이너에 복사합니다. OCR의 호출 방식과 런타임은 기존 백엔드와 함께 유지합니다.
- `all`은 기존 단일 이미지로 프론트와 백엔드를 함께 배포합니다.

`operation=restore`는 테스트 직전의 프론트와 백엔드 조합을 복원합니다. 프론트만 테스트했으면 컨테이너 재시작 없이 파일을 복원합니다.

### 업무 데이터 저장소 전환

과거 빌더 기록을 SQLite에 보존하고 새 기록만 기존 `a4b` 업무 표에 남기는 전환도 가능합니다.
먼저 DB 스키마·역할과 검증 이미지를 준비합니다. 기존 컨테이너를 정지한 뒤 실제 데이터 볼륨을 백업하고, 새 Docker 볼륨에 빌더 SQLite 파일과 관련 WAL/SHM/journal, 이전 식별자만 제외한 **나머지 파일 전부**를 권한과 함께 복사해 검증합니다.
새 볼륨 이름은 `a4b-fresh-`로 시작하며 `a4b.cutover.source` 라벨은 정지한 원본 컨테이너의 전체 ID입니다.
새 이미지에 이 볼륨을 `/app/data`로 마운트하고 평소와 같은 CA·앱 DB·데이터 경로로 `migrate-sqlite-to-postgres.py --confirm-empty`를 실행합니다.
이 절차에서 기존 기록이 복사되는 작업 공간 수는 0이어야 합니다. 원본 볼륨은 종전 컨테이너에 보존합니다.
운영 overrides에 `A4B_STORAGE=postgres`와 `A4B_BUSINESS_RECORDS=a4b`를 적용하고,
`scripts/deploy/a4b-ssm-release.sh "$CUTOVER_IMAGE" "$PUBLIC_URL" production "$FRESH_VOLUME"`로 전환합니다.
배포 스크립트는 사전 검사와 실제 새 컨테이너에 같은 볼륨을 사용하며 이후 일반 배포도 그 마운트를 재사용합니다.

공개 주소의 첫 SQLite → PostgreSQL 전환은 점검 시간에 진행합니다. 이후 PostgreSQL → PostgreSQL 배포는 기존처럼 실행 중인 컨테이너를 대상으로 합니다.

1. 수정된 배포 스크립트가 `main`에 있는지 확인합니다. 전환할 SHA의 백엔드 또는 전체 이미지를 준비하고, EC2에서 그 이미지 참조를 `CUTOVER_IMAGE`로 설정합니다. root 전용 `CUTOVER_ENV` 파일에 앱 DB 주소·CA 등 접속 설정과 **기존 컨테이너와 같은** `EASYFLOW_DATA_DIR`를 넣습니다. 관리자 접속 주소는 스키마를 적용하는 명령에만 환경 변수로 전달합니다.
2. 앱과 같은 볼륨에 쓰는 워커를 모두 정지합니다. `docker stop a4b-jhd-aws` 뒤 컨테이너는 보존합니다. `docker inspect a4b-jhd-aws --format '{{json .Mounts}}'`로 실제 마운트를 확인하고 볼륨과 대상 DB를 백업합니다.
3. 아래 명령을 EC2의 Bash에서 실행합니다. `--volumes-from`으로 정지한 앱의 볼륨을 그대로 사용합니다. dry-run에 나온 회원·표 건수와 고아 행을 백업과 대조한 뒤 실제로 복사합니다.

   ```bash
   : "${CUTOVER_IMAGE:?검증된 이미지 필요}" "${CUTOVER_ENV:?root 전용 Docker env 파일 필요}"
   run=(docker run --rm --network host --volumes-from a4b-jhd-aws --env-file "$CUTOVER_ENV")
   python=/app/Backend/.venv-agent/bin/python
   "${run[@]}" -e A4B_MIGRATION_DATABASE_URL "$CUTOVER_IMAGE" "$python" /app/scripts/deploy/apply-builder-migrations.py
   "${run[@]}" "$CUTOVER_IMAGE" "$python" /app/scripts/deploy/migrate-sqlite-to-postgres.py --dry-run
   "${run[@]}" "$CUTOVER_IMAGE" "$python" /app/scripts/deploy/migrate-sqlite-to-postgres.py
   ```

   루트 작업 공간에도 업무 DB가 있으면 두 이전 명령에 `--include-local`을 붙입니다. 확인한 볼륨에 기존 업무 DB가 하나도 없는 신규 설치는 마지막 명령에 `--confirm-empty`를 붙입니다.
4. 기존 앱을 정지한 채 EC2의 `/var/lib/a4b-deploy/env-overrides`(root 소유, 권한 600)에 `A4B_STORAGE=postgres`와 필요한 DB 설정을 반영합니다. Actions에서 `operation=deploy`, `branch=main`, `target=all`로 같은 SHA를 배포합니다. 프런트 단독 배포와 테스트 브랜치 배포는 이 정지 상태에서 진행할 수 없습니다.
5. 새 이미지가 스키마·권한, 볼륨과 RDS의 이전 식별자, 이전 시작 시각이 마지막 SQLite 정지 후인지 검사합니다. 사전 검사나 교체 후 헬스 체크가 실패하면 기존 SQLite 컨테이너는 정지 상태를 유지합니다. 문제를 해결하고 같은 백업 기준으로 재시도합니다. 성공 후 로그인해 기존 자료와 예약을 확인하고 점검을 종료합니다.

중간에 기존 SQLite 서비스를 다시 열어 쓰기가 발생했다면 새 점검 시간을 잡고 대상 DB를 이전 전 백업으로 되돌린 뒤 다시 이전합니다. PostgreSQL 전환 후 새 데이터가 생긴 경우 SQLite로 복귀하기 전에 해당 쓰기를 별도로 보존해야 합니다. [업무 데이터 저장소](business-data-storage.md)
