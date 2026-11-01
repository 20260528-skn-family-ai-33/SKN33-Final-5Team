# 내일 DB·파서 연결 순서

## 1. 파서 담당

CSV/PDF/Excel 원시 결과를 `Document`로 정규화합니다. JSON 예제는 `examples/inputs/01_ready.json`의 documents를 보세요. 파일 처리 자체는 이 패키지에서 실행하지 않습니다. 실패 시 자료를 만들어 채우지 말고 `failed` 또는 `partial`을 반환합니다. 페이지·표·원문 구간 위치를 locator에 보존하고 문서 ID와 버전을 안정적으로 부여합니다.

`Document.id`, 초안의 `document_ids`, 읽기 노드의 `file_ids`는 같은 자료 ID를 사용하도록 매핑합니다. 이 패키지가 실제 파일 경로나 DB 내부 PK를 추측해 바꾸지 않습니다.

```python
from a4b_verifier.adapters import document_from_parser
document = document_from_parser(normalized_parser_result)
```

## 2. DB 담당

`SQLiteStore`는 저장·정확한 버전 조회·결과 저장을 보여주는 연결 예제입니다. 운영 MySQL/PostgreSQL 등은 같은 동작을 repository 메서드로 구현하세요. 벡터 검색이 근거 일부만 반환하면 전체 문서를 검증했다고 표시하지 마세요. 실제 DB 스키마·마이그레이션은 별도 합의 대상입니다.

문서의 (id,version) 내용은 덮어쓰지 않습니다. 검증 결과에는 request_id, 초안 전/후 ID·버전, 문서 버전·근거, 모델 이름·모델 digest, 계약 버전, 판정·코드 진단·소요시간을 연결하는 것이 좋습니다. 예제 SQLite는 문서와 결과 두 테이블만 제공하며 모든 운영 이력 설계를 대신하지 않습니다.

## 3. 메인 모델 담당

```python
from a4b_verifier.adapters import draft_from_backend
candidate = draft_from_backend(main_agent_draft, draft_id="draft-001", version="2")
```

이 함수는 확인한 `agent_builder.models.AgentDraft`의 workflow·document_ids·전역 execution_plan을 투영합니다. 새로운 스키마면 별도 어댑터를 작성하세요. 생성 모델이 말한 ‘정답’이나 성공 설명을 검증 정답으로 전달하지 않습니다.

## 4. 검증 호출

```python
from a4b_verifier import VerificationInput, verify
request = VerificationInput.model_validate(payload)
result = verify(request, mode="live")
if result.route == "repair_plan":
    # 메인 모델에 issues/findings를 전달해 새 버전 초안을 받고 다시 검증합니다.
    pass
elif result.route == "ask_user":
    # 메인 모델이 missing_conditions를 바탕으로 질문합니다.
    pass
```

다른 분기와 횟수 제한은 메인 제어기에서 처리합니다. 예제는 무한 수정·자동 재시도를 하지 않습니다. 최종 실행 전 기존 실행기의 도구별 검증·승인 조건을 그대로 적용해야 합니다.

## 5. 첫 연결 시 확인할 사례

1. 01_ready: 명확한 보증금 조건 → 다음 검토
2. 02_missing_selection: ‘큰 금액’의 기준 없음 → 사용자 확인
3. 04_wrong_column: 보증금 요청인데 임대료 초안 → 초안 수정
4. 05_reflection_wrong_operator: ‘초과’ 답변을 gte로 반영 → 초안 수정
5. 06_reflection_correct: gt로 정확히 반영 → 다음 검토
6. 10_parser_failed: 실제 파싱 실패 → 검증 보류
7. 12_forged_evidence_guard: 가짜 모델 근거 → 검증 보류

내일은 먼저 fixture로 저장·조회·전달을 확인한 후 live를 사용하세요. 실제 모델이 틀린 사례는 기록하고, 계약 연결 성공과 모델 의미 정확도를 분리해서 보고합니다. 이 예제들을 학습하면 독립 평가셋으로 다시 쓰지 않습니다.
