# SVM 설치·진단 검증 — 2026-11-04

기준 main: `d3406ccc5742c274a39420bf0d20e0a5e1890be6`.

## 문제가 있던 부분

- main의 기본값은 `hint / svm56`이나 모델 파일은 Git에 포함되지 않는다. 새 clone에는 파일이 없어 추천이 준비되지 않는다.
- 예전 `disabled-by-default` 주석이 현재 설정과 달라 혼동을 만들었다.
- 파일·해시·Python 의존성 확인과 실제 모델 추천을 같은 명령으로 구분해서 확인하기 어려웠다.
- EC2 배포는 기존 환경변수와 마운트를 유지하므로 main 배포만으로 SVM 설치/활성 상태가 보장되지 않는다.

## 변경한 내용

- `npm run ml:install -- --source <압축을 푼 묶음>`: 기준 모델과 카탈로그 해시를 검사하고 기본 `runtime/ml-bundle`에 설치한다. 다른 기존 모델을 덮어쓰지 않는다.
- `npm run ml:check`: 실행 설정, 모델·카탈로그 존재/해시, 실제 Python 패키지 버전을 확인한다. 모델 역직렬화는 하지 않는다.
- `npm run ml:check -- --probe`: 검사 통과 후 서비스와 같은 추천 진입점으로 기존 CSV 진단 요청 1건을 처리한다. 규칙 보완 후보만 나왔을 때는 SVM 성공으로 처리하지 않는다.
- 로컬/Compose/EC2 확인 방법과 기준 모델의 미학습 도구를 문서화하고 낡은 주석을 수정했다.
- 모델 자체와 추천 정책, 서비스 환경변수, 기존 배포 스크립트는 바꾸지 않았다. 모델 바이너리는 별도 팀 전달 ZIP이다.

## 확인 결과

- 관련 Python 검사: **119 passed** (`Backend/tests/ml`). 신규 설치·진단 검사는 23개이다.
- 최초 검사 실행은 새 폴더의 임시 경로 부모 디렉터리 미생성으로 setup 오류가 났다. 실행 경로를 준비한 뒤 위 119개가 통과했다.
- 새 main 사본의 설치 전 상태: `missing_files`.
- 기준 모델 설치: `installed`; 같은 파일 재설치: `already_installed`.
- 명시적 `off`: `disabled`, 종료 코드 1.
- 존재하지 않는 모델 경로 지정: `missing_files`, 종료 코드 1.
- 실제 모델 진단: `svm_candidates_verified`, 종료 코드 0. `filter_sort`의 출처는 `svm56`이며 margin은 약 0.86685였다. `read_table`·`write_result`는 `service_contract` 보완 후보로 구분됐다.
- `node --check scripts/svm-bundle.mjs`, `git diff --check` 통과.
- 실제 모델과 카탈로그의 해시는 main 고정 해시와 일치했다.

## 아직 확인하지 않은 부분

- 운영 서버의 현재 환경변수, 모델 마운트, Python 환경은 이 로컬 검사로 확인하지 않았다.
- 운영 컨테이너에서 같은 진단을 실행한 뒤 실제 설계 trace의 `tool_advice_status`와 `source=svm56`을 확인해야 한다.
- 이번에 Qwen 서버를 호출하거나 워크플로를 생성·실행하지 않았다. 추천 성능을 새로 평가한 작업도 아니다.
- 기존 기준 모델은 `reply_review`, `review_summary`, `validate_document_format`, `email.attachment.get` 4개가 미학습이다. 후속 후보 모델 교체는 버전과 평가 결과를 별도로 확인해야 한다.

팀 설치 안내: [SVM 모델 전달·설치·연결 확인](../svm-deployment.md).
