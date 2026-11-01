# A4B Qwen 분류·검증 연동 패키지 v0.1

이 폴더는 **메인 워크플로우 생성 모델과 별개인 Qwen3.5-4B 검증기**를 DB·파서와 연결하기 위한 시작 패키지입니다. 실제 프로젝트 파일·DB를 변경하지 않습니다. BF16 LoRA SFT는 아직 수행하지 않았습니다.

**전달 상태:** 자동 검사 17개·fixture 13건 통과. 실제 Qwen 연결 3건은 JSON 응답 3/3, 의미·기대 경로 일치 1/3이었습니다. 틀린 2건은 코드가 보류했습니다. **연결 점검용이며 모델 품질 합격본·자동 실행용이 아닙니다.** 상세 결과는 `docs/VERIFICATION.md`를 먼저 읽으세요.

## 포함 범위

- 버전 고정 입출력 계약과 JSON Schema 3종
- Ollama `qwen3.5:4b` 기본 호출: 생각 모드 꺼짐, temperature=0, JSON 구조화 출력
- 요청 상태·누락 조건·초안 일치·답변 반영·근거 판정을 분리
- 실제 열·필수값·등록 도구·DAG·입출력 포트·정확한 인용·응답 모순 검사
- 파서 정규화 → SQLite 저장·동일 버전 조회 → 검증의 연결 예제
- 대표 사례 13건(의미 사례 11건 + 의도적으로 잘못된 모델 응답의 방어 검사 2건)
- 자동 회귀 검사와 교차검증 기록. fixture 검사는 모델 성능 측정이 아닙니다.

이 패키지는 업무를 실행하지 않습니다. `execution_allowed`는 **항상 false**입니다. `continue_review`는 메인 시스템의 추가 검사 단계로 진행한다는 뜻이며 실행 승인이 아닙니다. 코드 검사는 모든 업무 의미·모든 도구 설정을 완전 검증하지 않습니다.

## 1. 준비

Python 3.10 이상에서 폴더를 연 뒤 실행합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Windows PowerShell의 가상환경 활성화는 `.venv\Scripts\Activate.ps1`입니다. 패키지를 다른 프로젝트에서 import하려면 `python -m pip install -e .`를 사용합니다.

## 2. 모델 없이 연결 확인

```bash
mkdir -p local_runs
python -m a4b_verifier verify --input examples/inputs/05_reflection_wrong_operator.json --mode fixture --fixture examples/model_outputs/05_reflection_wrong_operator.json --db local_runs/demo.sqlite --output local_runs/fixture_result.json
```

먼저 `local_runs` 폴더를 생성하세요(`mkdir local_runs`). fixture는 사람이 작성한 예제 응답을 넣는 방식입니다. Qwen을 호출하지 않으며 그 결과를 모델 정확도로 보고하면 안 됩니다. 위 사례는 사용자 조건이 충분한 ready이지만, ‘초과’를 ‘이상’으로 반영한 초안을 `repair_plan`으로 보냅니다.

전체 계약 예제 검사:

```bash
python scripts/run_suite.py --mode fixture --output local_runs/fixture_suite.json
```

`run_suite.py`는 의미 판정까지 정답과 다르면 live에서도 종료 코드 1을 반환합니다. 이는 연결 실패와 구분해서 보고서를 읽어야 합니다.

## 3. 기본 Qwen 실제 호출

Ollama를 설치하고 모델은 담당자가 별도로 준비합니다. 패키지는 서버를 자동 실행하거나 모델을 자동 다운로드하지 않습니다. 서버 실행·모델 준비를 확인한 뒤:

```bash
python -m a4b_verifier health
python -m a4b_verifier verify --input examples/inputs/01_ready.json --mode live --output local_runs/live_result.json
python scripts/run_suite.py --mode live --cases 01_ready,02_missing_selection,05_reflection_wrong_operator --output local_runs/live_smoke.json
```

기본 주소는 `http://127.0.0.1:11434`, 모델은 `qwen3.5:4b`입니다. 변경하려면 `A4B_OLLAMA_BASE_URL`, `A4B_QWEN_MODEL` 환경변수를 사용하세요. 실제 문서가 외부 서버로 전송되는지는 **주소 설정에 따라 결정**됩니다. 이 배포본에는 키·환경 파일·모델 가중치가 없습니다.

기본 문맥 32,768·출력 상한 2,048, 호출 제한 180초, 재시도 0회입니다. 이는 연동용 고정 설정이며 최적 성능 설정을 찾은 결과가 아닙니다. 초과 입력은 잘라내지 않고 보류합니다. 문맥 검사는 UTF-8 바이트 기반의 보수적 상한이어서 실제 토큰보다 엄격할 수 있습니다. 관련 원문을 줄일 경우 버전·출처와 검증 범위를 호출 측에서 관리하세요.

## 4. 팀별 연결 위치

| 담당 | 연결할 내용 |
| --- | --- |
| 파서 담당 | 파서 원시 출력을 `Document`로 매핑. 열·샘플 행·문서 버전·원문 위치·ready/partial/failed 제공 |
| DB 담당 | 문서의 정확한 버전 조회, 원문 구간 보존, 검증 결과·초안 버전·모델 정보 저장. SQLite 예제는 운영 migration이 아님 |
| 메인 모델 담당 | 요청·대화와 실제 이전/수정 초안을 전달. 진단에 따라 질문 또는 초안 수정. 다른 포맷은 명시적으로 변환 |
| 검증 담당 | `verify()` 호출과 코드 진단·모델 진단을 함께 확인. 새 데이터에서 상태·누락·반영을 따로 평가 |

세부 필드·반환 경로는 [docs/CONTRACT.md](docs/CONTRACT.md), 연결 예제는 [docs/INTEGRATION.md](docs/INTEGRATION.md), 검산 기록은 [docs/VERIFICATION.md](docs/VERIFICATION.md)를 보세요.

## 5. 중요한 범위

- Qwen은 새 워크플로우·사용자용 질문을 작성하지 않습니다.
- `ready`와 `draft_verdict=pass`는 다릅니다. 사용자 조건이 충분해도 초안의 열·값·연산자는 틀릴 수 있습니다.
- 파싱 실패·일부 근거만 조회한 상황은 지원 불가와 구분합니다.
- 동적 `agent_node`·`saved_workflow`는 추가 검토로 보내며, 다른 도구도 코드 검사 범위는 부분적입니다.
- 원문 인용 검사는 존재 여부를 확인합니다. 의미가 맞는지는 독립 정답·추가 평가가 필요합니다.
- 다른 DB/파서 제품의 실제 연결 완료를 보장하지 않습니다. 이 패키지의 실제 DB 확인은 로컬 SQLite 예제입니다.
- 등록부는 저장소 `57476c7`의 16종 스냅샷입니다. 실서비스 등록부·활성 상태가 바뀌면 호출 측에서 같은 계약으로 갱신해야 합니다.

## 근거

[When2Call](https://aclanthology.org/2025.naacl-long.174.pdf): 실행·질문·지원 불가 판정의 참고 근거.
[Schema-Guided Dialogue](https://ojs.aaai.org/index.php/AAAI/article/download/6394/6250): 대화 조건 상태 비교.
[ToolSandbox](https://arxiv.org/html/2408.04682): 중간 상태·금지 동작 검증.
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs): JSON Schema 출력.
이 코드는 A4B용 자체 설계이며 논문 재현 구현이 아닙니다.
