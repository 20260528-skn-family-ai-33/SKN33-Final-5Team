# 기준 데이터와 후보 학습

기준은 사용자가 지정한 `A4B_ML_Qwen_전체작업자료_2026-10-31`의 `08_서비스연동패키지`입니다. 원본 5,431건, 56개 라벨 순서, 5-fold 분할, 미확인 -1 마스킹, char TF-IDF + LinearSVC, 임계값 0을 보존합니다. 서비스 22개 도구로 원본 라벨을 임의 변환하지 않습니다.

`supplemental_cases.json`은 검수·검수 보고서·표 계산·문서 검색·복합 요청의 합성 보강 48건(24개 그룹)입니다. 사람이 확정한 정답이 아니며 `human_approved=false`로 내보냅니다. 명시한 필수 도구는 1, 명시한 불필요 도구는 0, 나머지는 -1입니다. 새 응대 검수와 기존 검수 결과 보고서는 서로 다른 작업으로 대조 라벨을 둡니다. 미확인 라벨을 음성으로 바꾸면 안 됩니다. 같은 그룹의 표현은 같은 검증 fold에만 들어갑니다. 분할 ID와 원본 해시는 학습 전에 검사합니다.

```sh
python -m pip install -r Backend/requirements/ml-svm56.txt
python Backend/ml/training/train_candidate.py \
  --baseline /absolute/path/to/08_서비스연동패키지 \
  --output /absolute/path/outside-baseline/candidate
```

기준 폴더 내부에는 출력할 수 없습니다. `PLAN.json`, 보강 JSONL, `RESULTS.json`, OOF 예측, `candidate.joblib`을 별도 생성합니다. 기존 기록의 OOF와 비교할 때 원본 5,431건의 지표를 비교하고 새 합성 사례는 별도로 봅니다. 합성 보강은 대부분 부분 라벨이므로 해당 지표를 전체 도구 정밀도·최종 성공률로 부르면 안 됩니다.

이미 검토한 개발 fold에서 모델을 선택하는 과정이며 독립 테스트가 아닙니다. 원본에도 사람 검수 정답·실행 검증이 없습니다. 통계가 좋아져도 일반화나 실제 앱 실행 개선을 입증하지 않습니다. 원본의 복합 누락·취약 도구 오류는 별도 사람 검토 대상으로 유지하세요.

복합 요청 누락을 줄이는 개발 실험은 별도 출력 경로와 `--compound-positive-weight 2`로 실행합니다. 학습 fold의 복합 요청에서 확인된 양성만 가중하며 미확인 라벨은 여전히 제외합니다. 기본값 1은 가중하지 않습니다. 시도한 후보 전부를 기록하고 검증 fold로 선택한 결과를 독립 성능으로 보고하지 마세요.

자동 생성 모델은 학습 누락 해소와 개발 비교용 후보입니다. 서비스 hook은 기본적으로 원래 모델 해시를 사용하며 이 스크립트는 hook·해시·환경 설정을 변경하지 않습니다. 후보를 선택하려면 모델 경로와 예상 SHA256을 명시해야 합니다.

## 오추천 감소 실험

`--feature-mode hybrid_char`는 문자 TF-IDF와 단어·구절 TF-IDF를 함께 사용합니다. 두 블록의 제곱 가중치는 80:20이고, 기본 `char`의 동작은 유지됩니다. `hybrid`(50:50), `char_long`(문자 2~6-gram)도 개발 비교 대상으로 기록하며 성능이 낮은 실험을 숨기지 않습니다.

도구별 기준값 선택은 평가 fold와 분리합니다.

```sh
PYTHONPATH=Backend python -m ml.training.tune_candidate \
  --baseline /absolute/path/to/08_서비스연동패키지 \
  --output /absolute/path/to/new-experiment-directory \
  --feature-mode hybrid_char --base-weight 2
```

원본의 외부 5-fold 안에서 학습 데이터만 다시 그룹 단위 3-fold로 나눕니다. 내부 OOF에서 도구별 가중치 1/2와 고정 기준값 목록을 비교하고, 내부 기준 모델보다 확인된 오추천을 늘리거나 양성을 놓치는 정책은 선택하지 않습니다. 양성 20건 미만인 도구는 기준값을 맞추지 않습니다. 이 조건이 외부 평가에서 오추천 0 또는 무조건적인 개선을 보장하지는 않습니다.

각 fold의 내부·외부 ID와 정책, 학습 전 계획을 보존합니다. 최종 모델은 도구별 `thresholds`를 포함하며 서비스 추론기도 같은 값을 적용합니다. 선택에 이미 살펴본 개발 자료를 사용하므로 nested 방식이어도 독립 최종 평가나 사람 정답이 되지는 않습니다.

저장된 원본·후보 OOF의 비교 및 오류 CSV 생성:

```sh
PYTHONPATH=Backend python -m ml.training.compare_candidates \
  --baseline /absolute/path/to/A4B_ML_Qwen_전체작업자료_2026-10-31 \
  --candidate /absolute/path/to/experiment \
  --output /absolute/path/to/new-comparison-directory
```

여러 `--candidate`를 지정할 수 있습니다. 미확인 -1 추천은 오추천으로 세지 않으며 별도로 기록합니다. 입력 누락·중복 ID·라벨 불일치는 평가를 중단합니다. 원본 데이터의 라벨은 수정하지 않습니다.

## 첨부 조건이 없는 원본 자료의 평가

원본에는 실제 업로드 파일 ID/본문 대신 `provided_inputs_ko` 설명이 있습니다. 전체를 무조건 첨부 없음으로 해석하지 않습니다. `declared_inputs.py`는 이 설명만으로 파일 유형이 명확한 경우를 구분하며, 출력 저장 경로·이미 추출된 본문·OCR 결과·JSON 형태 미정·추가 인라인 입력 등은 별도로 남깁니다. 정답 라벨이나 operation 키는 이 변환의 입력이 아닙니다.

```sh
PYTHONPATH=Backend python -m ml.training.evaluate_declared_assets \
  --baseline /absolute/path/to/08_서비스연동패키지 \
  --candidate /absolute/path/to/oof-experiment \
  --output /absolute/path/to/new-attachment-evaluation
```

이 명령은 모의 메타데이터를 평가에만 사용합니다. 실제 서버에 파일이 업로드됐다고 등록하지 않습니다. 모호하거나 파일과 인라인 입력이 섞인 경우 독점적인 자료 유형을 가정하지 않습니다. 생성된 메타데이터를 정답을 보기 전에 저장하고 SHA256을 기록합니다.

평가 가능한 공통 16개 라벨의 일치도와 실제 업무 성공률은 다릅니다. 원본 56개 작업군의 `compose_answer` 등은 제공된 본문을 전제로 하는 경우가 있지만 앱 실행은 `read_document` 연결이 필요합니다. 이 차이를 자동으로 원본 라벨 오류로 판정하거나 전체 점수가 올라갔다고 주장하지 마세요. 실제 파일 파싱·저장·reader 검사는 `Backend/tests/ml/test_resource_context.py`에서 따로 수행합니다.


### 실험 산출물 검증 계약
학습 출력은 반드시 새 디렉터리를 사용합니다. 실패한 디렉터리는 진행 기록만 보존하며 재사용하지 않습니다.
학습기 두 종류 모두 fold 모델·학습/검증 ID·예측의 fold 번호·보강 입력을 보존하고, 모든 파일 저장 후 `COMPLETE.json`을 마지막에 기록합니다.
비교기와 첨부 조건 평가기는 완료 표시, 원본/산출물 SHA256, 고정 분할과 보강 그룹의 학습/검증 분리를 확인한 뒤 실제 fold 모델로 held-out 예측을 재계산합니다.
이 계약 이전에 생성한 실험은 자동 승인하거나 매니페스트만 덧붙이지 말고 새 폴더에서 재학습해야 합니다.
joblib 역직렬화가 포함되므로 신뢰하는 로컬 학습 산출물에만 사용하세요. 이 계약은 악의적으로 학습 이력을 위조한 작성자까지 인증하는 장치가 아닙니다.
기본 개발 환경에서는 ML 선택 의존성이 없으면 학습 전용 테스트를 건너뜁니다. 학습 검증에는 `Backend/requirements/ml-svm56.txt`를 설치해야 합니다.
