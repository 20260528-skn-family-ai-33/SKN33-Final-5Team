# SVM 모델 전달·설치·연결 확인

SVM 추천 코드는 main에 있지만 모델 파일은 Git과 Docker 이미지에 들어 있지 않습니다.
`hint / svm56` 설정만으로 모델이 설치되지는 않습니다. 아래 순서로 **각 실행 환경**을 확인합니다.

## 팀 공유 파일

기존 팀 패키지 `A4B_ML_Qwen_전체작업자료_2026-10-31/08_서비스연동패키지` 또는
별도 전달한 `A4B_SVM56_서비스설치_20261104.zip`을 풀어 사용합니다.

| 파일 | SHA256 |
| --- | --- |
| `model/recommender_char_svm_5431.joblib` | `df14a313659394fcc9b9efda431da7643e692ad4b33ee923f02edc97cc1dbbea` |
| `catalog/catalog_56.json` | `cd956b51658051ecd95c642b5738238b3ae7f819c89799668280ba6d96f5dd2a` |

두 파일 모두 필요합니다. 모델에는 문자 TF-IDF 전처리와 SVM이 함께 저장되고,
카탈로그는 도구 ID와 라벨 순서를 확인하는 데 사용합니다. 추론 코드와 Python 의존성은 main을 사용합니다.
서비스 추론에 원본 학습 데이터는 필요하지 않습니다. SHA 일치는 배포할 버전의 일치 확인이며 정확도 보증이 아닙니다.

## 로컬 설치

저장소 루트에서 실행합니다. `npm run setup`으로 서비스 Python 환경을 준비합니다.
이미 환경이 있으면 `.env`의 `A4B_AGENT_PYTHON`이 그 환경을 가리키는지 확인합니다.

```sh
npm run ml:install -- --source "/absolute/path/to/extracted-bundle"
npm run ml:check
npm run ml:check -- --probe
```

Windows에서도 경로를 따옴표로 감싸서 사용합니다. 기본 설치 위치는 저장소의 `runtime/ml-bundle`입니다.
설치 명령은 고정 해시의 두 파일만 복사하고 manifest를 작성합니다. 기존 다른 모델을 덮어쓰지 않으며
`.env`를 수정하거나 모델 다운로드·재학습·서비스 재시작을 하지 않습니다. 동일 파일이 있으면 `already_installed`를 반환합니다.

npm 진단 명령은 루트 `.env`를 읽고 `A4B_AGENT_PYTHON` 또는 `Backend/.venv-agent`를 사용합니다.
현재 프로세스에 이미 지정된 환경변수가 `.env`보다 우선합니다. 원래 서비스 Python과 같은 경로로 확인해야 합니다.

```dotenv
A4B_TOOL_ADVICE_MODE=hint
A4B_TOOL_ADVICE_BACKEND=svm56
```

기본 위치를 사용하면 모델/카탈로그 경로는 생략합니다. 기존 `A4B_SVM_MODEL_PATH`와
`A4B_SVM_CATALOG_PATH`가 있으면 기본 설치 위치보다 우선하므로 잘못된 옛 경로가 남아 있지 않은지 확인합니다.
후보 모델은 평가 결과의 정확한 해시를 함께 지정해야 하며, 설치 명령은 기준 모델만 지원합니다.

## Docker에서 확인

Compose를 쓰는 경우 `.env`의 `A4B_ML_BUNDLE_DIR`에 위 묶음의 절대 경로를 지정합니다.
기본값은 `./runtime/ml-bundle`이며 컨테이너의 `/app/ml-bundle`에 읽기 전용으로 연결됩니다.
기존 `A4B_WITH_ML=0`으로 만든 이미지는 의존성 설치가 빠졌으므로 `1`로 빌드해야 합니다.

컨테이너가 실행 중인 상태에서 **읽기 전용 진단**:

```sh
docker compose exec -T -w /app/Backend app /app/Backend/.venv-agent/bin/python -m ml.svm56.bundle_cli check --probe
```

EC2처럼 Compose 대신 기존 컨테이너 설정을 이어받는 배포는 다음과 같이 확인합니다.

```sh
docker exec -w /app/Backend a4b-jhd-aws /app/Backend/.venv-agent/bin/python -m ml.svm56.bundle_cli check --probe
```

현재 `scripts/deploy/deploy-a4b-ec2.sh`는 기존 컨테이너의 환경변수와 bind mount를 이어받습니다.
**코드 배포만으로 기존 `off` 설정이나 빠진 모델 마운트가 바뀌지 않습니다.** 운영 담당자가
현재 모델 경로와 마운트를 확인한 뒤 배포 절차에 맞춰 반영해야 합니다. 진단 명령은 이를 자동 변경하지 않습니다.
기존 배포가 마운트 개수를 제한하므로 모델 마운트를 임의로 추가하지 말고 배포 설정과 함께 검토합니다.

## 결과 읽는 법

| status | 의미 | 다음 확인 |
| --- | --- | --- |
| `disabled` | 명시적으로 `off` 설정 | 실제 서비스 환경변수 |
| `different_backend` | 다른 추천기 사용 중 | `A4B_TOOL_ADVICE_BACKEND` |
| `missing_files` | 모델 또는 카탈로그 없음 | 파일 설치·경로·마운트 |
| `hash_mismatch` | 기대한 모델/카탈로그와 다름 | 전달 버전과 파일 해시 |
| `dependency_mismatch` | 실행 환경의 패키지 누락/버전 차이 | `Backend/requirements/ml-svm56.txt` |
| `ready_for_probe` | 설정·파일·의존성 확인, 모델 로딩 전 | `--probe`로 실제 추천 확인 |
| `probe_failed` | 실제 추천 확인 실패 | JSON의 `probe.ml_status`·`error_type` |
| `svm_candidates_verified` | 기존 진단 요청에서 실제 SVM 후보 생성 | 실제 설계 trace에서 Qwen 전달 확인 |

파일 검사만 하는 `check`는 joblib를 실행하지 않습니다. `--probe`는 해시·의존성 확인 후
서비스와 같은 추천 진입점에서 기존 CSV 진단 요청 1건을 처리합니다.
`source=service_contract`는 코드로 보완한 후보이고 `source=svm56`이 실제 SVM 후보입니다.
보완 후보만 나왔을 때는 SVM 성공으로 처리하지 않습니다. 실패는 종료 코드 1, 성공은 0입니다.
진단은 인증키나 전체 환경변수를 출력하지 않습니다. 파일 경로는 출력하므로 외부 전달 시 필요하면 가립니다.

이 명령은 실행한 프로세스만 확인합니다. 개발자 PC에서 성공해도 배포 서버의 활성화를 뜻하지 않습니다.
배포 서버에서 진단 후 실제 설계의 `tool_advice_status`와 `tool_advice_candidates`에서
`source=svm56`을 확인하면 Qwen에 전달한 추천을 구분할 수 있습니다.
SVM 추천이 준비돼 있어도 부정·취소·여러 사용자 발언은 기존 정책에 따라 요청별로 보류할 수 있습니다.

## 모델 범위

이번 전달은 main이 이미 고정한 기준 모델의 설치입니다. 새로운 재학습 모델 교체가 아닙니다.
56개 라벨 중 52개 분류기가 학습돼 있으며 `reply_review`, `review_summary`,
`validate_document_format`, `email.attachment.get`은 이 기준 모델에서 미학습입니다.
규칙으로 보완한 검수 도구 추천을 학습 모델의 성능으로 표현하지 않습니다.
점수는 확률이 아닌 SVM margin입니다. AI 참고 라벨의 개발 검증 결과이며,
이 진단은 모델 정확도 평가·Qwen 생성·워크플로 실행 성공 검증을 대신하지 않습니다.
