# 프론트엔드 수정 및 재현 검증 (2026-10-29)

이 문서는 Agent Studio의 실제 진입 화면(`Frontend/app/main.tsx` → `agent-studio.tsx`)을 기준으로 한다. 테스트의 모델 고정 응답·합성 입력을 실제 LLM 업무 정확도로 해석하지 않는다.

## 수정 내용

- 초기 복원은 명시적 service URL → 기존 대화 → 마지막 서비스 순서로 처리한다.
- 새 작업·서비스 선택·수정 이후 도착하는 이전 응답과 오류를 차단한다.
- 설정 수정 대화 생성 성공 시 service URL을 제거해 새로고침 시 해당 수정 대화를 복원한다.
- 저장 성공과 후속 목록 조회 실패를 구분한다. 상세 조회 실패는 재저장 없이 다시 불러올 수 있다.
- 수정·저장 경계에서 이전 실행 파일·응대 초안·요청을 초기화한다. 예약의 즉시 실행은 숨겨진 실행 파일을 전달하지 않고 관리 소스를 사용한다.
- 재개 응답을 정상 수신하면 확인 답변을 초기화하며 네트워크 실패 시 재시도를 위해 보존한다.
- 노드 편집 시 기존 추가 필터·정렬·예외 정책·숨겨진 settings를 보존한다. 단순 숫자 입력 폼으로 표현할 수 없는 첫 필터는 원형을 유지한다.
- 노드 저장의 중복 제출과 저장 중 폼 변경·화면 이동을 제한한다. 목록 갱신 실패는 읽기 재시도로 복구한다.
- 실행 기록의 오래된 상세 조회·오류가 다른 선택이나 실행을 덮어쓰지 않도록 차단한다.
- API 오류를 노드 화면에서도 표시하며, 모든 파일 업로드 진입점에 8MB 사전 검사를 적용한다.

## 기본 검사

```sh
npm --prefix Frontend ci
npm --prefix Frontend run check
npm --prefix Frontend test
```

`npm test`는 먼저 프로덕션 빌드를 수행한다. Python 관련 API 테스트가 생략되지 않도록 `Backend/.venv-agent`를 설치하거나 `A4B_AGENT_PYTHON`에 잠금 버전 환경의 절대 경로를 지정한다. Docling 통합 테스트는 준비된 `EASYFLOW_DOCLING_PYTHON`을 설정했을 때 실행한다.

DOM 검증에는 개발 의존성 jsdom을 사용한다. esbuild로 실제 React 컴포넌트를 불러와 클릭·입력·폼 제출·상태 복원·오류 복구를 검사한다. 실제 브라우저 엔진의 레이아웃이나 모바일 터치를 검증하는 E2E는 아니다.

## 격리된 실제 API + React DOM 검증

기존 사용자 DB 대신 새로운 `a4b-ui-*` 임시 디렉터리를 사용한다. 이 검증은 실제 워커·SQLite·합계 계산·다운로드 바이트·노드 버전 저장을 실행하며 외부 모델을 호출하지 않는다.

1. 저장소 루트에서 프로덕션 빌드를 완료한다.
2. 터미널에서 `EASYFLOW_DATA_DIR`을 새 임시 디렉터리로, `HOST=127.0.0.1`, `PORT=3149`, `A4B_AGENT_PYTHON`을 준비된 환경으로 설정하고 `Frontend/build-server/index.mjs`를 실행한다. 사용자 환경 파일을 읽지 않는 테스트 전용 프로세스를 사용한다.
3. 해당 로컬 서버의 `POST /api/credentials`에 테스트 전용 계정을 생성한다. 예시 JSON: `{"name":"UI review fixture","provider":"openai","data":{"apiKey":"browser-fixture-not-a-real-key"}}`. 실제 키를 사용하지 않는다.
4. 응답의 `credential.id`로 다음 합성 자료를 생성한다. 스크립트는 기존 워크플로우가 있는 DB에 쓰지 않는다.

```sh
PYTHONPATH=Backend /absolute/agent-python Frontend/scripts/seed-frontend-review.py /absolute/a4b-ui-fresh-directory TEST_CREDENTIAL_ID
```

5. 다음 환경변수로 `npm --prefix Frontend test`를 실행한다.

```text
A4B_AGENT_PYTHON=/absolute/agent-python
EASYFLOW_DOCLING_PYTHON=/absolute/parser-python
A4B_FRONTEND_REVIEW_URL=http://127.0.0.1:3149
A4B_FRONTEND_REVIEW_IDS=/absolute/a4b-ui-fresh-directory/browser-fixture-ids.json
```

6. 결과를 보존한 후 테스트 전용 서버만 종료한다. RunPod는 사용하지 않는다.

## 예제 파일 출처

문서 정리 커밋 이후 테스트가 계속 참조하고 있던 예제 7개를 Git 커밋 `57476c7`의 원본 바이트로 복원했다. 테스트 기대값을 변경하거나 임의의 통과용 자료로 대체하지 않았다.

- 초보자 CSV·Excel·PDF 예제
- xlsx-filter 및 pdf-text 워크플로우 JSON
- 요청으로 파일 받기 및 요청에 결과 돌려주기 워크플로우 JSON

## 검증 한계

자동 검사 통과는 모든 가능한 입력에 대한 무오류 보장이 아니다. 실제 브라우저의 시각·모바일·접근성 사용성 검증, 실제 외부 모델의 설계 품질 및 외부 서비스 운영 인증은 별도다. 현재 세션에서는 Work 설정에서 로컬 브라우저 사용이 비활성화되어 실제 브라우저 검증은 수행하지 못했다.


## 최신 main 통합 후 확인 결과

2026-10-29, main `777c77f`(PR #12)를 반영한 로컬 통합본에서 확인했다.

- 전체 프론트엔드·서버·API·DOM: **672개 통과, 실패 0, 생략 0**.
- 백엔드 설계·실행·문서 분석·Qwen 계약: **766개 및 하위 검사 17개 통과**.
- TypeScript, 프로덕션 클라이언트·서버 빌드, `git diff --check` 통과.
- 기존 상태 관리 함수 24개와 새 문서 분석·검토 기능을 함께 보존했다.
- 새 문서 검토 다이얼로그 열기·검토 완료·부모 자료 갱신도 React DOM에서 확인했다.
- 실제 API와 연결한 합계·JSON 보고서 다운로드·입력 누락 오류·예약 소스·노드 버전 저장을 재검증했다.

문서 분석의 원본 리더 환경은 `Backend/requirements-document-analysis.txt`로 별도 준비한다. `A4B_DOCUMENT_PYTHON`으로 해당 환경을 지정할 수 있다. 백엔드 단위 테스트가 원본 리더를 직접 import할 경우 이 환경의 site-packages도 테스트 Python에서 읽을 수 있어야 한다. 이 검증에는 외부 LLM 호출이나 RunPod 실행이 포함되지 않았다.
