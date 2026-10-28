# 미지원 요청과 임시 도구 실험 연결

2026-10-27 / 개발 경로. 기존 워크플로우 저장·실행 권한을 확대하지 않는다.

## 현재 동작

1. 설계 그래프가 unsupported로 종료할 때 `tool_gap` 노드가 요청·미지원 이유·자료 ID/버전·등록 도구 16종과 해시를 체크포인트에 기록한다.
2. 이 판단은 `unverified`다. 파싱·DB 입력 장애이면 명세를 내보내지 않는다. 기존 도구나 도구 조합으로 가능한데 모델이 놓쳤을 수도 있다.
3. 화면의 **부족 기능 명세 받기**는 현재 대화 버전을 확인한 후 JSON을 내려받는다. API는 `GET /api/agent-builder/tool_gap?id=<session UUID>&revision=<현재 정수 버전>`이다. 모델 호출이나 워크플로우 저장을 하지 않는다.
4. 검토자는 전체 등록 도구와 대조하고, 해당 요청 중 어떤 기능을 검토했는지 `action_index`로 지정한다. existing_tools/unclear/external_integration/input_problem이면 생성 대신 재설계·질문·연동 설계로 돌린다.
5. actual_gap이고 동기식 순수 JSON 변환 명세가 확정됐을 때만 기존 도구 실험실로 전달한다. 하나의 검토에는 하나의 명세와 공개·숨긴 정답 사례를 연결한다. 미정 조건이 남으면 모델을 부르지 않는다.
6. 생성·문법 검사·QuickJS 실행·정답 비교는 기존 실험실을 재사용한다. 수정 상한 0~2회. 모델에는 공개 예시만 전송하며 숨긴 입력·정답은 전달하지 않는다.
7. 생성 후 같은 대화·자료·도구 해시를 재검사한다. 변경됐으면 시험 통과 여부와 별개로 `stale`을 기록한다. 성공해도 `passed_lab_tests`이며 미등록 상태다.

입력 장애는 시스템 복구 대상으로 유지한다. 외부 이메일·Slack 전송, 네트워크·파일·DB 쓰기는 이 변환 실행기의 지원 범위가 아니다. 임시 변환 코드를 만들 수 있다는 사실로 외부 연동 권한까지 생기지는 않는다.

## 검토 규격

`Frontend/server/tool-lab/gap-bridge.ts`의 `reviewSchema`가 실제 규격이다.

- `protocol`: `a4b.tool-gap-review.v1`
- 명세에서 복사: `session_id`, `revision`, `gap_id`, `state_fingerprint`, `catalog_sha256`
- `classification`: actual_gap / existing_tools / unclear / external_integration / input_problem
- `action_index`: reported_missing_actions 중 검토한 항목의 0부터 시작하는 번호
- `reviewer`, `review_basis`(human / automated_development), `reason`
- `compared_tool_ids`: 현재 등록 도구 전체 ID
- `existing_tool_ids`: 기존 도구로 해결 가능하면 해당 ID. 하나라도 있으면 코드 생성 차단
- `effects`: 현재 실행 가능 값은 `["json_transform"]`뿐
- `reviewed_spec`: 평가 dataset의 spec과 완전히 같은 객체. 입출력 열·자료형·기준·오류 정책·미정 조건 포함

자동 개발 검토를 사람 검수로 표시하면 안 된다. `human` 표시는 운영자가 제공하는 검수 진술이며 시스템이 사람의 전문성·명세 의미의 타당성을 증명하지 않는다. 검토자가 원래 요청을 다른 업무로 바꾸지 않았는지 별도로 확인해야 한다. 모델의 unsupported 텍스트만으로 실제 기능 누락을 확정하지 않는다.

## 실행

Frontend에서 명시적으로 로컬 Ollama 모델을 선택한다. 서비스 기본 모델·계정·환경 파일은 변경하지 않는다.

```sh
npm run tool-lab:gap -- --review /절대경로/review.json --dataset /절대경로/dataset.json --model qwen3.5:4b --output /절대경로/시험기록 --repairs 1
```

현재 서비스 저장소에서 같은 session/revision을 다시 읽는다. 서비스와 다른 개발 저장소를 시험하려면 실행 환경의 `EASYFLOW_DATA_DIR`를 그 경로로 지정한다. JSON으로 전달받은 gap만 믿고 모델을 실행하는 경로는 제공하지 않는다. 현재 CLI는 로컬 Ollama만 연결하며 외부 API 연결은 기존 독립 실험실에서 별도 비교한다.

`dataset`은 `a4b-tool-lab-v1`, 코드는 `transform-function-v2`다. 자세한 실행 제한은 [도구 실험실](tool_lab.md)을 따른다. 고객 자료·사용자 요청이 로그에 남을 수 있으므로 기존 로그를 비밀 저장소로 취급하지 않는다.

## 결과와 미완료 범위

각 실행은 기존 코드 후보·해시·호출 로그·사례 결과와 함께 gap.json/gap-review.json/gap-evaluation.json/source-gap-bridge.json을 기록한다. 개발 실행의 근거와 결과는 작업공간 `research/a4b_tool_gap_20261027`에 보관한다.

다음은 아직 구현 완료가 아니다.

- 사용자 요청만으로 부족 기능의 정확한 의미·입출력·정답 사례를 모두 자동 확정하는 기능
- 임시 도구를 원래 저장 워크플로우에 자동 연결해 사용자 자료를 실행하는 기능
- 시험 통과 후보의 자동 정식 등록·SVM 라벨 추가·자동 승격
- Python 코드 생성·실행, 외부 API/DB 권한 연동
- 새 사람 검수 평가셋의 일반화 성능과 실제 팀 서비스 통합

기존 등록 도구로 가능한 업무는 기존 검증·사용자 확인·저장 흐름을 유지한다. 기본 deepagent/v1/조건 검사 off를 바꾸지 않았다. 미지원 명세 기록·내보내기는 그래프에 추가했지만 코드 생성은 별도의 명시적 실험 요청이다.

## 후속: 사용자 선택 연결

2026-10-27 후속 코드에서는 제작 전 현재 사용자의 create_tool 선택을 요구한다. 기존의 명세 검토만으로 모델을 호출하던 실험 실행 조건에서 변경됐다. [선택/제작 연결 규격](tool-gap-choice-handoff/README.md)을 따른다. gap CLI는 executeReviewedGap을 통해 같은 동의의 중복 작업을 막고 결과를 대화 저장소에 보존한다. 과거 consent가 없는 합성 DB/검토 파일로 재실행하면 거부되는 것이 현재 정상 동작이다. 과거 검증 기록의 모델 점수는 당시 경로의 기록으로 유지한다.
