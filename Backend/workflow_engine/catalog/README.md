# 56개 업무 도구와 서비스 노드

`business56.json`은 전달된 56-label 서비스 카탈로그의 ID·이름·설명이다.
`unified_tools/catalog.json`에는 같은 56개 ID의 실제 동작과 구현 범위를 보존했다.
기존 `agent_node`, `finish`, `condition`, `ask_user`, `saved_workflow`, `write_result`는
제어·호환 도구이므로 서비스 등록 수는 62개이며 ML 업무 라벨은 56개다.

공통 계약 `canonical.py`를 노드 스키마, 생성기, 저장 전 검증, 개별 노드 라이브러리와
실행기가 함께 사용한다. 기존 도구의 `operation=null`은 기존 설정·포트를 유지한다.
확장 동작은 `settings.operation`, JSON 객체 문자열 `settings.arguments_json`을 사용한다.
상수는 JSON 인자로, 이전 결과는 실행 함수의 인자명과 같은 `bindings.input`으로 전달한다.
필수·알 수 없는·중복 인자, 미등록 동작, 없는 포트, 실행 순서가 잘못된 연결은 보류한다.

Ollama에서는 `arguments_json`을 실제 JSON 객체로 생성한 뒤 저장할 때 문자열로
정규화한다. 생성 스키마는 도구의 개별 operation마다 실제 인자명을 제한한다.
기존 노드는 실행기가 읽는 설정만 생성하고 나머지는 앱이 기본값으로 복원한다.
전체 62개 ID는 항상 조회할 수 있으며, 문맥 제한을 위해 처음에는 지원 판정이
선택한 도구와 종료 도구의 생성 규격을 전달한다. JSON/목록 선택을 포함한 다른 도구를
`inspect_tool`로 조회하면 그 규격도 즉시 추가된다. 추천 누락이 도구 삭제로 이어지지 않는다.

- `result`: 구현 함수가 반환한 원시 JSON. `text`: 표시용 결과.
- `table`, `documents`, `asset`, `artifact` 등은 동작 계약이 보장하는 경우만 제공한다.
- JSON 결과의 표 선택은 `json_transform.lookup`과 `settings.output_port="table"`로
  명시한다. 선언한 표·목록·텍스트 형식은 실행 결과 검증으로 다시 확인한다.
- 중첩 인자가 필요한 경우 `{"$binding":"입력 인자명"}`을 쓴다. 예를 들어
  `create_spreadsheet.create`의 `sheets=[{"name":"Result","table":{"$binding":"sheets"}}]`에
  `input=sheets`로 앞 단계 `table` 출력을 연결하면 실제 표로 XLSX를 만든다.
- 업로드 원본은 `{"asset_id":"실제 연결 자료 ID"}`로 지정한다. 원본을 복사하고
  해시를 확인해 `operation-files/` 안에서 처리한다. 반환된 `asset`은 다음 파일 작업에 연결한다.
- DOCX·XLSX·PDF 등은 base64 표기를 포함한 artifact로 보존하고, 보고서 다운로드는
  실제 바이트를 반환한다. 원본·중간 산출물과 최종 보고서를 혼동하지 않는다.

모든 ID 등록이 모든 형식·동작의 무제한 지원을 뜻하지는 않는다. 동작별 `scope`가
실제 범위다. 예를 들어 문서 생성은 텍스트·Markdown·DOCX이고, 형식 변환은 구현된
변환 쌍만 허용한다. 모델 동작에는 현재 실행 모델이 필요하고, 시각 QA에는 이미지
입력을 지원하는 모델이 필요하다. 품질을 규칙 검증만으로 보장하지 않는다.

Gmail/Google Calendar는 서버의 `A4B_GOOGLE_SENDER_EMAIL`, `A4B_GOOGLE_ACCESS_TOKEN`으로
연결한다. 토큰을 노드 설정에 넣지 않는다. 미연결이면 계정 연결 필요로 실행을 중단한다.
HTTP 링크 확인은 `A4B_LINK_ALLOWED_HOSTS`의 명시적 허용 호스트만 HEAD로 확인한다.
카탈로그 조회·검증은 실제 메일 발송이나 일정 변경을 수행하지 않는다.

설치: `npm run setup`은 `requirements/agent-lock.txt`를 통해 `canonical-tools.txt`도 설치한다.
Docker 역시 같은 목록을 사용한다. 기존 환경은 setup을 다시 실행해야 한다.

검증:

```sh
Backend/.venv-agent/bin/python scripts/verify-business-tools.py
PYTHONPATH=Backend Backend/.venv-agent/bin/python -m pytest Backend/tests/workflow_engine/test_business56.py -q
Backend/.venv-agent/bin/python scripts/verify-business-tools-live.py --output /tmp/business56-live
```

56개 각각의 저장·재조회·실행, Deep Agent/direct 생성 응답의 수용,
이전 표 → 새 join → 기존 writer, 표 → XLSX 연결을 검증한다.
테스트 모델 응답과 외부 계정은 대체값을 주입한다. 실제 Qwen의 도구 선택 정확도,
실제 Gmail 발송·Calendar 변경을 검증한 것으로 해석하지 않는다.
`verify-business-tools-live.py`는 별도로 실행 중인 로컬 API(기본 3047)와 실제 모델을
사용한다. 계산·CSV 선별·URL 분석·XLSX·두 표 연결을 생성, 저장, 실행하고 결과를
확인한다. 외부 메일·일정 변경은 수행하지 않는다. 실패 기록도 report.json에 남긴다.
