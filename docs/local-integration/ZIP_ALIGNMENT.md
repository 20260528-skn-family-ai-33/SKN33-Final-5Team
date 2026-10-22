# ZIP 계약 대조 및 수정 범위

2026-10-21 로컬 통합본 기준. Downloads 원본과 원격 Git은 수정하지 않았다.

## 원본 대조

| 대상 | 확인 결과 |
| --- | --- |
| 오전 팀 통합자료의 `qwen_chat_model.py` | 실행 코드와 바이트 일치. SHA256 `d08b2c8c0c8ff421182b8c09727ce0ecdbbe874bc13c20a2cd1d99d5e1408a47` |
| 분류·검증 패키지 보존본 | 62개 파일 모두 Downloads 원본과 일치 |
| ML 참고 보존본 | 17개 파일 모두 Downloads 원본과 일치 |
| 실행 중인 검증기의 contracts / validation / service / adapters / registry 등 7개 파일 | 원본과 일치 |
| 검증기 SYSTEM 지시문 | 원본과 일치. 새 워크플로우·사용자 질문 생성 권한 없음 |
| 원본 예제 계약 | 현재 실행 검증기로 fixture 13/13 통과. 모델 정확도 평가는 아님 |

비교 기록은 `output/zip-contract-audit.json`, 예제 결과는 `output/zip-runtime-fixture-regression.json`에 있다.
자동 회귀 검사는 `test_zip_contract_alignment.py`에서 검증 계약·판정 코드·지시문의 변경을 감지한다.

## 통합에 필요한 변경

- 메인 모델 생성은 공통 팩토리로 연결한다. vLLM은 오전 ZIP의 QwenChatOpenAI와 ProviderStrategy를 사용한다. 스트리밍·사용량 수집·자동 재시도 제한도 연결 예시에 맞춘다.
- Ollama는 별도 호환 어댑터에서 원본의 `omit_empty_tools`를 재사용한다. vLLM 전용 structured_outputs/top_k 설정을 Ollama에 보내지 않는다. 최종 응답 계약은 동일한 AgentDraft다. 메인의 temperature=0.7/top_p=0.8은 ZIP 예시와 맞추고 검증기의 temperature=0은 원본 그대로 둔다.
- 검증기 `client.py`의 변경은 URL/provider/환경변수/인증 헤더/RunPod 앱 식별 헤더와 vLLM 호출 지원이다. 출력 계약·인용 검사·판정 우선순위를 변경하지 않는다.
- 모델 호출 시간과 전체 설계 시간을 환경변수로 제한한다. 실제 OpenAITimeoutError/OpenAIConnectionError 하위 타입도 구분해 안내하고 접속 키가 오류에 노출되지 않게 한다.
- 생성 초안은 기존 `validate → verification → supervisor`를 거친다. 검증 결과의 execution_allowed는 항상 false이고 저장에는 기존 검증과 사용자 승인이 필요하다.
- shadow는 결과를 기록하고 기존 제어 흐름을 덮어쓰지 않는다. enforce는 보류·불일치 시 저장을 차단한다. 임의 통과 처리나 사용자 조건 자동 보완을 추가하지 않는다.

도구 순서를 강제로 고정하는 시험 변경과 Ollama ToolStrategy 대체 경로는 최종 코드에서 제외했다.
모델 응답 형식 설명을 보완했지만 특정 열·기준값·정렬·질문·그래프를 업무 코드에 고정하지 않았다.

## 확인 한계

원본 ZIP도 통합 앱의 실제 생성 품질을 보장하지 않는다고 명시한다. RunPod에서 두 모델의 응답과 인증은 확인했지만 CSV 설계는 재질문으로 실행 전 보류됐다.
이후 로컬 추론 시험에서 시간 초과를 확인했고 해당 제한·오류 안내를 수정했다. 모의 응답·fixture 통과를 실제 모델의 업무 생성 성공으로 표시하지 않는다.
AWS DB·파서의 운영 연동은 이번 로컬 통합 완료 범위가 아니다.

## 파서 전달본 추가 통합

`parser-team-handoff-2026-10-21`의 SHA256 manifest 31개 항목을 검증하고
`Backend/integrations/parser_handoff`에 원본 그대로 보존했다. 실행 워커와 공통 모듈은 `Backend/scripts`에 연결했다.
기존 Native PDF 전용 코드를 전달본의 standard Docling + EasyOCR(ko/en) + 표 후처리 + PyMuPDF4LLM 보완으로 대체했다.
LightOn은 전달본대로 기본 꺼짐이며 CUDA 없이 자동 활성화하지 않는다.

- PDF 업로드의 원문·페이지·섹션·표 HTML/Markdown을 유지한다.
- `parser_status`와 `parser_metadata`(설정·코드 해시·본문/LightOn 보완 보고서·시간·실행 정보)를 자료 JSON에 보존한다.
- 원본 SHA256과 파일 보관 경로는 기존 앱이 생성한다. 파서가 AWS/S3 위치나 권한을 만들지 않는다.
- SQLite 저장 후에도 메타데이터를 보존하고, 검증기에는 실제 문서 ID·버전·페이지 본문과 파싱 상태를 전달한다.
- failed는 경고가 있어도 partial/ready로 승격하지 않는다. 부분 성공이나 본문 보완 미완료는 partial이다.
- 파서 인프라 메타데이터는 설계 모델에 보낼 업무 본문과 구분한다.
- 워커 응답과 DB 초안 계약(`a4b.parser_result.v0.1-draft`)은 별개다. DB 예시를 실제 처리 완료 문서로 등록하지 않는다.

제공된 DB 예제 두 개의 schema/semantic 검사는 통과했다. 삼성 67페이지 예시는
`whole_document_complete=false`를 유지한다. 원본 전체 87페이지 처리가 완료됐다고 표시하지 않는다.
전달본이 참조하는 `sources/docling-p67-document.json`, `sources/multilevel-reference.json`,
`chunk-example.json`, `index.html`은 포함되지 않아 전체 외부 근거 비교는 수행하지 않았다.
이를 채우려고 원문·좌표·청크·검증 성공을 만들어 넣지 않는다.

별도 전용 파서 환경과 공개 OCR/표 모델 캐시를 준비했다. 경로는 루트 `.env`에 설정하며,
`Backend/requirements-parser.txt`와 `prepare_parser_models.py`로 재준비한다.
파서 전달본의 회귀 검사 51개가 통과했고, 합성 PDF 실제 파싱에서 본문과 표 1개를 확인했다.
실제 앱 연결 결과는 `output/parser-app-bridge-smoke.json`에 기록한다.

## 재점검 결과

잘못된 parser_metadata/supplement_report 타입은 예외로 앱을 중단시키지 않고 검증 입력 계약 오류로 기록하여 manual_review로 보류한다.
추가 회귀 포함 Python 292개·하위 검사 13개가 통과했다.
실제 누락 본문 보완은 원문에 있는 문장을 복구했고 오류가 없었다.
텍스트 레이어가 없는 합성 PDF에서 한영 OCR이 동작했지만 1500원을 `1 5 0 0 원`으로 띄어 인식했다.
이를 임의로 교정하거나 OCR 정확도 보장으로 집계하지 않는다. 실제 문서의 금액·표 관계는 원문 대조가 필요하다.
메인 Qwen의 전체 업무 생성은 여전히 시간 초과가 남아 있다. `output/integration-recheck.json` 참조.
