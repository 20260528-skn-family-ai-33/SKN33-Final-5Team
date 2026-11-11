# 장부 근거 라벨 작성

`scripts/verify/score-qwen-ledgers.py`는 원본 결과와 고정 코퍼스에서 검토 틀을 만든다. 터미널에서 다음 순서로 실행한다. `RUN`, `GOLD`, `LABELS`, `RESULT`는 각 파일 경로로 바꾼다. 출력 파일은 새 이름을 사용한다.

```text
python scripts/verify/score-qwen-ledgers.py RUN --corpus GOLD --template --output LABELS
python scripts/verify/score-qwen-ledgers.py RUN --corpus GOLD --labels LABELS --require-complete --output RESULT
```

특정 사례 묶음은 `--case R04 --case M01`처럼 지정한다. 전체 결과는 고정 코퍼스의 고유 사례를 전부 측정하고 라벨을 채운 뒤 `--require-complete`로 집계한다.

각 사례의 `source_case_sha256`은 틀에서 가져온다. `evidence`에는 사용자 발화, 모델 응답 번호, 수용 단계 번호와 판정의 근거를 기록한다. `inventory`는 원본에서 계산한 후보·수용 업무 ID와 파싱하지 못한 requirements 응답 번호를 제공한다.

| 필드 | 기입 방법 |
|---|---|
| `owner_checks` | 소유 상태와 비교할 수 있는 후보 키마다 아래의 한 행을 작성한다. 전부 검토해 비교 기회가 없으면 `[]`이다. |
| `duplicate_candidate_ids` | `inventory.candidate_task_ids` 중 같은 후보 안의 의미 중복 두 번째 이후 ID를 기록한다. |
| `duplicate_accepted_ids` | `inventory.accepted_task_ids` 중 같은 수용 장부 안의 의미 중복 두 번째 이후 ID를 기록한다. |
| `required_work` | 최종 READY가 정답인 사례의 각 고정 task ID에 `present`, `omitted`, `not_reached` 중 하나를 기록한다. 장부 미도달은 그 사례의 전체 의무에 적용한다. |

다음은 합성 예시다. `candidate_call_index`는 **사례 내부** `model_calls`의 0부터 시작하는 인덱스다. `owner_key`에는 비교할 장부와 키를 쓴다.

```json
{
  "candidate_call_index": 2,
  "owner_key": "condition_ledger.slots.threshold",
  "candidate_conflict": true,
  "accepted_conflict": false,
  "conflicting_candidate_retained": false,
  "evidence": "사용자가 20으로 확정했고 후보는 30을 제안했다. 수용 장부는 20을 보존했다."
}
```

수용 상태의 근거가 없으면 `accepted_conflict`와 `conflicting_candidate_retained`에 `null`을 쓴다. `candidate_conflict=false`인 경우 `conflicting_candidate_retained`는 `null`이다. 모델이 30을 제안했으나 장부가 40을 저장한 경우처럼 둘 다 원문에 어긋나면서 서로 다른 값이면 `accepted_conflict=true`, `conflicting_candidate_retained=false`다. `null`과 검토한 빈 배열을 구분한다.

`parsed_state.requirement_ledger`를 실제로 반환한 requirements 단계의 항목만 수용 업무로 센다. 재사용한 기존 장부가 다음 노드 입력에 등장하는 것으로 수용 업무를 늘리지 않는다. 코드 recipe로 생성된 장부에는 후보 업무가 없을 수 있다. 보고 파일은 사례 해시, 원본 파일 바이트 해시, 라벨 바이트 해시를 통해 검토 근거에 연결한다.
