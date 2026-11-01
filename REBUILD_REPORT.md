# 기능별 재구성 보고서

- 작성일: 2026-11-01
- 원본: `C:\Users\munkyu\Desktop\SKN33-Final-5Team` (브랜치 `fit/mk`, 커밋 `5da5c3f`). 읽기 전용으로만 사용했고 수정하지 않았습니다.
- 대상: `C:\Users\munkyu\Desktop\final_test`. `git init`만 했고 커밋·브랜치·원격 설정은 하지 않았습니다.
- 요청에 따라 6번 항목(문서·연구 자료·생성물 재분류)은 진행하지 않았습니다. `docs/`, `images/`, 루트 `scripts/`는 원본 위치 그대로 복사했고, 코드 경로가 바뀐 부분만 수정했습니다.
- 아래의 모든 수치는 이번 작업에서 실제로 실행한 결과입니다. 과거 문서에 적힌 성공 기록은 사용하지 않았습니다.

## 1. 새 프로젝트 폴더 구조와 기능별 설명

```text
final_test/
├─ Frontend/            화면만 (app·components·hooks·lib·public·vendor·docs), UI 테스트 tests/, UI 검토 스크립트 scripts/
├─ shared/              화면과 API 서버가 함께 쓰는 코드: workflow.ts·catalog.json·node-references·item-provenance·document-review, api-contracts.ts(데이터 형식)
├─ Backend/
│  ├─ api/              Node API 서버(구 Frontend/server, TypeScript 그대로)
│  │  ├─ src/           서버 소스, tool-lab/
│  │  ├─ tests/         서버 테스트(서버+화면 컴포넌트 혼합 테스트 포함)
│  │  ├─ scripts/       setup-python·test-python·verify-*·live-check-*
│  │  └─ build-server/  번들 결과(Git 제외)
│  ├─ service/          Node→Python 작업 접수: worker.py(stdin/stdout JSON), dispatch.py(작업 표), handlers/{runs,library,reviews,schedules,design}.py
│  ├─ agent_builder/
│  │  ├─ design/        A1 설계: graph, nodes/(conversation·direct_conversation·supervisor·validation·save·await_input·verification), linear_plan, planner_constraints, presentation, preprocessing(+pilot/), experiments/
│  │  ├─ conditions/    condition_readiness, condition_check, input_resolution, input_requirements, clarification_contract
│  │  ├─ capabilities/  capability_gate, capability_judgement, capability_check
│  │  └─ tool_gap/      gap, choice, jobs, redesign
│  ├─ workflow_engine/
│  │  ├─ runner.py      저장된 노드·연결을 LangGraph로 컴파일·실행(구 workflow_runtime.py 중 실행부)
│  │  ├─ workflow.py    노드·연결 검증
│  │  ├─ legacy_runtime.py  이전 설정(test_agent) 실행 경로 — 사용 중이라 유지
│  │  ├─ executors/     dispatch.py(module_path 실행), schemas.py(모델 구조화 응답), agent_factory.py(Deep Agents 생성 지점), sources.py
│  │  │  ├─ table/      read_table, filter_sort, aggregate_table
│  │  │  ├─ document/   read_document, search_document, extract_information, compose_answer(A2)
│  │  │  ├─ agent/      agent_node, role.py(A3 역할 실행)
│  │  │  ├─ control/    condition, ask_user, finish, saved_workflow
│  │  │  ├─ output/     write_result, export_result, review_summary
│  │  │  ├─ review/     reply_review(A4 노드 래퍼)
│  │  │  └─ legacy_tools/  read_table·filter_sort·write_result 구현(실행기와 legacy 경로가 공유)
│  │  ├─ lifecycle/     run_lifecycle(선점·중단·재개·재시도)
│  │  ├─ scheduling/    schedules(예약·관리 자료)
│  │  ├─ library/       node_library(노드 버전 저장·참조), reusable_workflows
│  │  └─ catalog/       registry(도구·포트·EXECUTORS 표), output_contracts
│  ├─ ml/
│  │  ├─ advice/        tool_recommendation(off/hint 선택 진입점)
│  │  ├─ legacy/        policy/, model_bundle/tool_rec(기존 joblib가 의존하는 모듈명 유지), models/
│  │  └─ svm56/         inference_adapter, service_*_adapter, svm56_service_hook
│  ├─ documents/
│  │  ├─ analysis/      파서 선택·엔진 연결(MinerU·Docling·PaddleOCR)·결과 계약 (구 document_analysis)
│  │  ├─ parsers/       subprocess로 실행되는 worker와 후처리(구 Backend/scripts의 파서 파일)
│  │  ├─ resources/     document_resources(원문 위치·청크), quotes(인용 원문 확인)
│  │  └─ review/        document_review(사람의 파싱 결과 검토)
│  ├─ verification/
│  │  ├─ workflow/      구 a4b_verifier(계약·코드 검사·선택적 Qwen 검증)
│  │  └─ reply_review/  review, contracts, prompts, state, decisions(A4 검수·사람 수정·승인)
│  ├─ infrastructure/
│  │  ├─ llm/           model_factory, ollama_chat_model, qwen_chat_model, errors(public_error)
│  │  └─ persistence/   storage(SQLite), ids(uid·now), checkpoints(LangGraph SqliteSaver)
│  ├─ contracts/        models.py(AgentDraft·NodeSettings·ExecutionPlan 등 공유 규격)
│  ├─ tests/            pytest. 기능별 하위 폴더, fixtures/(parser_handoff·qwen_verifier 전달본 원형 보존)
│  ├─ scripts/          setup-*.sh, prepare_parser_models, verify_document_runtime, import-preprocessing, package-integrated, seed-frontend-review
│  ├─ requirements/     agent.txt·agent-lock.txt·parser.txt·document-analysis.txt·local-integration.txt·ml-legacy.txt
│  └─ pytest.ini        Backend/를 import 기준으로 설정, fixtures 수집 제외
├─ scripts/             루트 실행기·도구(원본 루트 scripts + launch-demo·start-agent-demo·실행.cmd/.command)
├─ docs/, images/       원본 그대로
├─ runtime/             실행 데이터·모델·로그 (Git 제외). models/svm56/에 ML 패키지 모델·카탈로그 배치
├─ package.json / package-lock.json   원본 Frontend의 파일을 루트로 이동(의존성·잠금 파일 내용 동일, scripts만 변경)
├─ tsconfig.json        tsx 실행용 '@/' 별칭(루트 실행 시). 타입 검사는 Frontend/·Backend/api/ 각자 tsconfig
└─ REBUILD_REPORT.md, REBUILD_FILE_MAP.md
```

### 결합 관계 처리
| 요청서의 결합 | 처리 |
| --- | --- |
| worker.py가 모든 작업을 분기 | `service/worker.py`는 입출력만 담당. `dispatch.py`의 작업 표가 기능별 handler로 연결. 설계 세션 분기 본문은 원본과 줄 단위로 동일함을 diff로 확인 |
| workflow_runtime에 스키마·근거 함수 혼재 | 응답 스키마 → `executors/schemas.py`, `original_quote` → `documents/resources/quotes.py`, `document_chunks` → `documents/resources/document_resources.py`, 실행 분기 `execute_node` → `executors/dispatch.py` |
| executors가 workflow_runtime을 역참조 | compose_answer의 `runtime.create_deep_agent` → `executors/agent_factory.py`. 스키마·dispatch 분리 후 실행기→runner 참조는 `saved_workflow`의 하위 워크플로우 컴파일(`compile_workflow`) 1곳만 남음(의도된 재귀 실행) |
| node_library의 저장과 역할 실행 혼재 | 저장·참조 검사는 `library/node_library.py`, 역할 실행은 `executors/agent/role.py` |
| storage 외 SQL | 기능 소유 테이블의 SQL(예약·승인·문서 검토·작업·노드 라이브러리)은 해당 기능 모듈에 남김. 연결·WAL·체크포인트 생성만 `infrastructure/persistence`로 모음(남은 작업 참고) |
| registry.module_path 문자열 | 문자열 조합 대신 `EXECUTORS` 표 하나로 관리. 실행 시 `dispatch.py`가 동적 import. DB `registered_tools` upsert가 module_path도 갱신하도록 수정(원본은 갱신 안 함, 읽는 곳 없음) |
| 파서 worker subprocess·파일 경로 | `documents/parsers/`로 이동, Node `backendPath(...)`와 `engine_worker`의 경로 수정 |
| 테스트 공통 도우미 import | 테스트 모듈 이름을 `tests.<영역>.test_x`로 바꿔 import 유지. 별도 support 모듈로 추출하지는 않음(남은 작업) |
| joblib가 tool_rec 모듈 경로에 의존 | 피클을 로드하지 않고 opcode만 검사: legacy 모델은 `tool_rec.features`/`tool_rec.models` 필요 → `ml/legacy/model_bundle/tool_rec` 이름 유지. SVM56 모델은 sklearn만 필요 |

## 2. 원본 파일 → 새 파일 대응 목록

전체 표(341행)는 [REBUILD_FILE_MAP.md](REBUILD_FILE_MAP.md)에 있습니다. 표의 모든 새 경로가 실제로 존재하는지 확인했습니다.

이동 방식:
- Python은 스크립트가 각 import를 원래 모듈 기준으로 해석한 뒤 새 모듈명으로 다시 썼습니다.
- 통째로 옮긴 패키지(verification/workflow, documents/analysis, ml/svm56)는 상대 import를 그대로 두었습니다. 그 결과 원본과 바이트 단위로 같고, 경로 깊이를 바꾼 줄만 다릅니다.
- 문자열 치환은 문자열 전체가 모듈 경로인 경우(예: `monkeypatch.setattr('agent_builder.reply_review.run_review')`)만 바꿨습니다. `'a4b.document_analysis.v1'` 같은 데이터 값은 보존했습니다.
- TS/JS는 각 상대 경로를 원래 파일 기준으로 해석한 뒤 새 위치 기준으로 다시 계산했습니다.

## 3. 프론트 보존 여부와 수정한 연결 파일

**화면 보존 결과**
- 원본과 새 프로젝트에서 각각 `vite build`를 실행해 비교했습니다.
- JS 번들의 SHA-256은 원본과 같습니다(`5c6ca8d2…`).
- `index.html`은 자산 파일명의 해시만 다릅니다.
- CSS는 규칙 3개(`.filter!`, `.inline`, `.order-42`)가 빠졌습니다.
  - 원인: Tailwind가 `Frontend` 안의 텍스트를 스캔하는데, 이 토큰들은 `Frontend/server/connectors.ts`와 `webhook-api.test.ts`의 문자열에서 우연히 만들어진 것이었습니다.
  - 화면 코드(app·components·hooks·lib)에서는 세 클래스를 사용하지 않음을 검색으로 확인했습니다. 화면 표시에는 영향이 없습니다.

**변경한 프론트 파일**
| 파일 | 변경 내용 | 이유 |
| --- | --- | --- |
| `app/agent-studio.tsx`, `app/document-analysis-summary.tsx`, `app/document-review-panel.tsx` | `import type` 경로 1줄: `../server/document-analysis-contract` → `../lib/api-contracts` | server가 Backend로 이동 |
| `app/workspace.tsx`, `components/flow/{assistant-dialog,connection-panel,error-agent,execution-view,node-editor}.tsx` | `import type` 경로: `@/server/{contracts,assistant,agents}` → `@/lib/api-contracts` | 같은 이유. 타입 전용이라 빌드 결과에 영향 없음(JS 해시 동일) |
| `lib/workflow.ts`, `lib/node-references.ts`, `lib/item-provenance.ts`, `lib/document-review.ts` | 구현을 `shared/`로 옮기고 한 줄 재내보내기만 남김 | API 서버도 실행 코드로 사용하는 공유 코드. 구현은 shared 한 곳에만 있음. 원본과 같은 내용임을 확인(document-review는 타입 import 1줄만 변경) |
| `lib/api-contracts.ts` (새 파일) | `export type * from '../../shared/api-contracts'` | 화면이 쓰는 API 타입의 진입점 |
| `tsconfig.json` | `include`에서 `server/**` 제거, `tests/**`·`../shared/**` 추가 | 폴더 분리 |
| `tests/frontend-dom.test.ts`, `tests/product-tour.test.ts` | `FRONTEND_ROOT`를 서버 paths 대신 파일 위치에서 계산 | 프론트가 백엔드 코드에 의존하지 않도록 |

- 변경하지 않은 파일: `vite.config.ts`, `postcss.config.mjs`, `index.html`, `public/`, `vendor/`, `components/ui/`, 화면 로직 전부.
- `shared/api-contracts.ts`의 타입은 원본 서버 파일에서 그대로 옮겼고, 서버 파일은 같은 이름으로 다시 export합니다. 그래서 API 요청·응답 형식은 바뀌지 않았습니다.
- Frontend/server는 Python으로 다시 쓰지 않고 TypeScript 그대로 `Backend/api/src`로 옮겼습니다. 번들과 소스가 같은 깊이에 있어야 `paths.ts`가 동작하므로 `src/` 아래에 두었습니다.

## 4. 보존한 기능과 확인하지 못한 기능

**자동 검사로 확인한 것** (원본과 같은 테스트가 새 위치에서 통과)
- CSV·Excel·JSON 표 입력 → 필터·집계 → 결과 파일 저장·다운로드 (Python workflow 테스트, Node api/templates/document-api 테스트)
- 설계 세션·워크플로우·실행 기록의 저장과 재조회, 노드 버전 라이브러리
- 질문 대기·재개·재시도, worker 시간 초과 뒤 같은 체크포인트에서 재시도 (worker-lifecycle 테스트)
- 문서 업로드·검토 상태 보존, A4 응대 수정·승인·거절과 승인 응답 내보내기 (reply-review-api, reused-review-api, document-review)
- 예약: 생성·선점·중복 방지·heartbeat (test_scheduling, schedule 테스트)
- ML 추천 기본값 `off`와 legacy 경로. SVM56 미설정·실패 시 전체 도구로 폴백 (test_tool_advice_failure, svm56 테스트)
- Qwen 검증 계약: 보존 패키지와 계약·판정 코드가 바이트 단위로 일치(test_zip_contract_alignment). 모의 응답 기반 검사이며 실제 Qwen 품질 검증은 아닙니다.
- 실서버 확인(빌드 결과, 임시 데이터 폴더): `/api/health` 응답, 화면 HTML·JS 제공, `/api/agent-builder/catalog`(Node→Python worker, 도구 16개, `module_path=workflow_engine.executors...`), 노드 목록, SQLite 3종이 지정 폴더에 생성됨

**확인하지 못한 것**
- 실제 모델(OpenAI·Ollama/vLLM Qwen)로 설계·실행: 모델 서버가 없어 미실행
- Docling·MinerU·PaddleOCR 실제 파싱과 OCR 품질: 파서 환경(`.venv-docling` 등)과 모델 가중치가 없음
  - `scripts/test-parser-handoff.py`는 `docling_core`가 없어 중단됨
  - Node의 Docling 연동 테스트 1개는 원본과 같이 skip
- Docker 이미지 빌드: Docker가 없어 미실행. Dockerfile은 정적으로만 수정
- 브라우저에서 사람이 직접 하는 E2E 조작: 미실행(DOM 테스트는 jsdom으로 통과)
- 온프레미스 실행: 미검증. 이번 작업은 구조 정리만 했습니다.
- 실제 SVM56 hint 모드 추론: 모델 파일은 배치했지만, 그 모드로 서버를 실행하지는 않았습니다.

## 5. 설치·실행 명령 (저장소 루트 기준)

```bash
npm ci
npm run setup            # Backend/.venv-agent 생성 + Backend/requirements/agent-lock.txt 설치
npm run dev              # 빌드 후 http://127.0.0.1:3030
```

| 용도 | 명령 |
| --- | --- |
| 빌드만 | `npm run build` (화면 → `Frontend/dist-app`, 서버 → `Backend/api/build-server/index.mjs`) |
| 빌드된 서버 실행 | `npm start` |
| Ollama/vLLM 설정 실행기 | `node scripts/run-configured.mjs` (루트 `.env`, 기본 포트 3041) |
| 간편 실행 | `npm run demo` 또는 `scripts/실행.cmd`·`scripts/실행.command` |
| 타입 검사 | `npm run check` |
| Node 테스트 | `npm test` |
| Python 테스트 | `npm run test:python` 또는 `cd Backend && .venv-agent/Scripts/python -m pytest -q` |
| 문서 파서 환경(선택) | `npm run setup:docling` |

- 환경 파일은 루트 `.env` 하나입니다. `.env.example`에 원래 Frontend 예시를 주석으로 합쳤고, SVM56 설정 예시를 추가했습니다.
- 원본은 `npm run dev`가 `Frontend/.env`를 읽었는데, 이번에 요청하신 대로 루트 `.env` 하나로 바꿨습니다.

## 6. 실행한 검사와 결과

| 검사 | 원본(같은 PC, 같은 버전) | 새 프로젝트 | 판정 |
| --- | --- | --- | --- |
| `npm ci` | 통과 | 통과(루트, 잠금 파일 동일) | 통과 |
| `npm run check` (tsc) | 통과 | 통과(Frontend·Backend/api 각각) | 통과 |
| `npm run build` | 통과 | 통과, JS 해시 동일 | 통과 |
| Node 테스트 | 704개: 703 통과·1 skip | 704개: 703 통과·0 실패·1 skip | 동일 |
| Python pytest (기본, Windows cp949) | 734 통과·48 실패·수집 오류 1 | 735 통과·49 실패 | 아래 설명 |
| Python pytest (`PYTHONUTF8=1`) | 777 통과·5 실패·수집 오류 1 | 779 통과·5 실패 | 실패 목록 동일 |
| 모든 Python 모듈 import | — | 138개 모듈 import 성공 | 통과 |
| 실서버 기동 확인 | — | health·화면·catalog·nodes 정상 | 통과 |
| 원본 절대경로 참조 | — | 없음(README의 GitHub 저장소 주소만 존재) | 통과 |
| `scripts/validate-parser-handoff.py`, `verify-clarification-handoff.py` | — | 통과 | 통과 |
| `scripts/test-parser-handoff.py` | — | docling_core 없음 | 미실행(환경) |
| Docker build | — | Docker 없음 | 미실행 |

**Python 실패 해석**
- 원본에 이미 있던 실패: 대부분 Windows 기본 인코딩(cp949)에서 `read_text()`가 UTF-8 파일을 못 읽는 문제입니다. UTF-8 모드에서는 사라집니다. 남는 5개는 아래 두 가지입니다.
  - `test_pipeline::test_csv_quoted_newline…`: Windows의 CRLF 처리 문제
  - `test_killed_process_recovers…` 4개: Windows에서 프로세스를 강제 종료한 뒤 SQLite가 "disk I/O error"를 냄
  - 두 가지 모두 원본에서도 같습니다. killed_process 테스트는 원본에서 3번 반복했을 때 3/4, 4/4, 3/4 실패로 결과가 바뀌어서, 기본 모드 비교에서 생긴 1개 차이(`filter_sort-0`)도 이 변동 때문입니다.
- 원본 문제를 고친 것: `document_analysis/test_resources.py`가 `from Backend.agent_builder...`를 import해서 Backend 폴더에서 실행하면 수집조차 안 됐습니다. 새 위치에서는 통과합니다.
- 구조 변경 때문에 생겼다가 고친 문제(최종 0건):
  - monkeypatch 대상 변경(`worker.build_graph` → `service.handlers.design`, runner의 `create_deep_agent` → `agent_factory`)
  - 테스트 경로 깊이
  - 보존본 비교 경로
  - Node 테스트 안에 들어 있는 Python 코드의 모듈명
  - tsx가 JSX 설정을 적용하지 않던 문제

## 7. 남은 작업과 실행에 필요한 모델·환경

1. **실제 모델 검증**
   - 메인 모델: OpenAI 계정 또는 Ollama/vLLM의 `qwen3:8b-q4_K_M`
   - 검증 모델: `qwen3.5:4b`
   - 서버를 띄운 뒤 `.env`의 `A4B_LLM_BASE_URL` 등을 설정하고 `npm run verify:review:live`, 설계·실행 시나리오 확인
2. **문서 파서 환경**
   - `npm run setup:docling`(torch·docling 등 대용량) 후 `Backend/scripts/prepare_parser_models.py`로 모델 가중치 준비
   - MinerU·PaddleOCR는 별도 환경(`A4B_MINERU_PYTHON`, `A4B_PADDLE_PYTHON`, 모델 경로)이 필요합니다.
   - 준비되면 `python scripts/test-parser-handoff.py`와 `npm run verify:...`를 실행합니다.
3. **SVM56**
   - 모델·카탈로그를 `runtime/models/svm56/`에 두었고, 해시는 코드에 고정한 값과 일치합니다.
   - 사용하려면 `.env`에 `A4B_TOOL_ADVICE_MODE=hint`, `A4B_TOOL_ADVICE_BACKEND=svm56`, `A4B_SVM_MODEL_PATH`, `A4B_SVM_CATALOG_PATH`를 넣습니다.
   - 학습 환경과 같은 버전이 필요합니다(Python 3.13, scikit-learn 1.6.1 — `requirements/ml-legacy.txt` 버전과 일치).
   - 모델 점수는 확률이 아니고 독립 테스트도 없습니다(패키지 README 기준).
4. **Docker 이미지 빌드와 컨테이너 실행 확인**
   - 원본 Dockerfile은 `a4b_verifier`·`document_analysis`를 이미지에 넣지 않았습니다(원본 문제). 새 Dockerfile에는 포함했지만 빌드는 확인하지 못했습니다.
5. **구조 후속 정리(선택)**
   - 기능 모듈 안에 흩어진 SQL을 `infrastructure/persistence`의 저장소 계층으로 모으기
   - 테스트 공통 도우미를 `tests/support/`로 추출하기
   - `contracts/models.py` 중 설계 전용 `BuilderState`를 design 안으로 옮기기
6. **RDS 전환(이번 범위 밖)**
   - `참고/database.zip`의 PostgreSQL 16 + pgvector(1024차원) + S3 스키마(v2 18개 테이블 + verification_checks, AWS에 이미 적용)는 현재 SQLite 25개 테이블과 대응표가 없습니다.
   - 전환하려면 테이블 대응과 데이터 이관 설계가 먼저 필요합니다.
   - 배포 스크립트에 실제 RDS 엔드포인트가 들어 있어 새 프로젝트에는 복사하지 않았습니다.
7. **Windows 개발 환경 참고**
   - 원본 테스트 다수가 cp949에서 실패하므로 Windows에서는 `PYTHONUTF8=1` 사용을 권장합니다(코드 수정은 하지 않음).

## 8. 원본에 비해 제외한 파일과 제외 근거

| 제외 | 근거 |
| --- | --- |
| `.git/`, `.idea/`, `.pytest_cache/`, `__pycache__/`, `*.pyc` | 버전 기록·편집기·캐시 |
| `.venv-parser-test/`, `Frontend/node_modules/` | 설치 환경. 잠금 파일로 다시 만들 수 있음 |
| `Frontend/dist-app/`, `Frontend/build-server/` | 빌드 결과 |
| `output/` 전체 | RunPod 요청·상태 JSON, 로그, 벤치마크 결과, 과거 검토용 소스 압축본(`parser-benchmark-code.tgz` 등), 모델 휠. 생성물·로그·과거 복사본이고 실행 코드가 참조하지 않음. 필요한 스크립트는 실행 시 `output/`을 다시 만듦 |
| `Backend/parser/` | `__pycache__`만 있고 소스가 없음 |
| 실제 `.env`, 사용자 데이터·SQLite·업로드 | 원본에 없었고, 있었더라도 복사 대상이 아님 |
| `Frontend/.env.example` | 루트 `.env.example`에 합침 |
| `ml/*.zip`, `참고/*` | 프로젝트 옆에 둔 전달 자료(Git 제외). ML 모델·카탈로그만 `runtime/models/svm56/`에 복사 |

---

### 사전 점검에서 확인한 사항(참고)
- ML 압축본: 서비스용 모델은 `df14a313…`이고, 원본 코드에 고정된 해시는 `c515ff1b…`였습니다. 요청하신 대로 코드를 `df14a313…`로 바꿨습니다.
- `ml/svm56`의 추론 어댑터는 압축본과 줄바꿈만 다르고 내용은 같습니다.
- 바꾸지 않은 것: ML 기본값(`off`·`legacy`), SQLite 저장 방식, 검색 구현(단어 기반, 벡터 검색 아님).

## 9. 추가 반영: PR #17 (로컬 도구 6종) — 2026-11-01

[원본 저장소 PR #17](https://github.com/20260528-skn-family-ai-33/SKN33-Final-5Team/pull/17)(열림·미병합, 기준 `kjj/parser-qwen-integration` `b5ca0ed`)을 새 구조에 옮겨 적용했습니다. 원본 폴더는 수정하지 않았습니다.

- 반영: 새 노드 `calculate_columns`·`text_transform`·`base64_codec`·`text_hash`·`date_transform`·`text_statistics`(9개 동작), 모델 규격·레지스트리·워크플로우 검증·실행 결과·설계 프롬프트·도구 판단 수정, direct 설계 경로의 SVM 힌트 전달, 화면 도구 이름·설명.
- 새 구조에서만 한 일: `EXECUTORS` 표에 6개 실행기 등록, 상대 import를 새 위치로 수정(`ml.advice.tool_recommendation`, `workflow_engine.unified_tools`).
- 반영하지 않음: PR의 `tool_recommender_svm56/` 4개 파일과 `tool_recommendation.py`의 svm56 분기(PR #16과 동일하며 이미 `ml/svm56`에 있음, 모델 해시는 더 최신인 `df14a313…` 유지).
- `workflow_engine/unified_tools/`는 PR의 v4 통합 도구 코드를 그대로 옮겼습니다. 카탈로그에 있는 9개 동작만 호출되며 Google·원격·모델 호출 코드는 포함만 되어 있습니다.

검사 결과
- Node 703/704 통과(실패 0), 타입 검사·빌드 통과. 서버 카탈로그가 22개 도구를 제공함을 확인했고 계산 실행기를 직접 실행해 결과와 수식 내 코드 실행 차단을 확인했습니다.
- Python: 처음 옮긴 직후에는 이전 실패 5개 외에 23개가 새로 실패했습니다(원본 HEAD에 PR만 적용한 사본에서도 동일). PR 기준 브랜치 이후에 추가된 계약과의 충돌이었고, 아래처럼 맞춘 뒤 793개 통과·실패 5개(재구성 전과 같은 CRLF CSV·강제 종료 SQLite 항목)로 돌아왔습니다.
  - 검증기: 전달본과 바이트 비교되는 `verification/workflow/registry.json`은 그대로 두고, 연결부 `agent_builder/design/nodes/verification.py`의 `LOCAL_SCOPES`에 새 도구 6개의 검증 범위(필수 입력 포트·필수 설정, `basic`)를 명시했습니다. 열 의미 검사는 기존처럼 메인 검증이 맡습니다.
  - 확인 질문 계약: `scripts/export-clarification-contract.py`로 `docs/clarification-handoff/`의 도구 카탈로그와 예제를 다시 만들었습니다(모델 호출 없음). 바뀐 것은 카탈로그 지문, 도구 목록 6개 추가, 출력 예제 2개의 `extraction` 필드입니다. `extraction`은 PR이 아니라 현재 코드와 이전 문서의 차이입니다. `provenance.json`·`verification.json`은 과거 기록이라 그대로 두었습니다.
  - 도구 수 16을 고정한 테스트 5곳을 현재 카탈로그 크기와 비교하도록 바꿨습니다.
- 실제 Qwen으로 새 도구를 사용하는 설계·실행은 확인하지 않았습니다.

## 10. 설계 속도 개선 — 2026-11-01

같은 요청("엑셀 판매 표에서 지역별 매출 합계를 구해서 파일로 저장해줘")을 서버 API로 보내 설정별로 측정했습니다(설정마다 1~2회, Qwen 답변 길이에 따라 편차가 있음).

| 설정 | 첫 실행 | 연속 실행 |
|---|---|---|
| 기존(KV 압축 없음, 검증 shadow) | 83초 | 92초 |
| KV 캐시 q8_0 + 검증 defer (적용) | 33초 | 21~25초 |

- Ollama 사용자 환경변수에 `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`을 추가했습니다. 메인 모델이 37/37층 GPU에 올라가 생성 속도가 초당 약 7.5토큰에서 약 31토큰으로 올랐습니다.
- 검증 모드 `defer`를 추가했습니다(`agent_builder/design/nodes/verification.py`). 요청 때는 shadow와 같은 검증 입력만 저장하고, `scripts/run-deferred-verifications.py`(`verification/deferred.py`)가 나중에 판정을 채웁니다. 설계 경로·되묻기에는 영향이 없습니다. 로컬 `.env`를 `defer`로 바꿨고 `.env.example` 기본값은 `shadow` 그대로입니다.
- 실제 대기 기록 3건을 일괄 실행해 판정이 채워지는 것을 확인했습니다(약 27초, 모두 `manual_review`).
- 테스트 4개 추가, Python 797개 통과·실패 5개(기존 항목).
- 도구 목록 직접 전달(`A4B_TOOL_CATALOG_MODE=direct`)은 측정 결과 모델이 바로 긴 초안을 만들어 오히려 느려져 적용하지 않았습니다.
- q8_0의 답변 품질 영향, 파서가 붙었을 때의 시간은 확인하지 않았습니다.

### 10-1. 첫 요청 지연과 "파일 없음" 즉시 안내

- Ollama 사용자 환경변수 `OLLAMA_KEEP_ALIVE=-1`: 쉬었다 보낸 첫 요청에서 모델을 다시 불러오던 지연(로그의 `loading model`)을 없앴습니다. `ollama ps`에서 메인 모델이 `Forever`·100% GPU로 상주함을 확인했습니다.
- `agent_builder/design/attachment_precheck.py`: 최근 사용자 문장이 이미 올린 파일을 가리키고("올려준·첨부한·업로드한…" + "파일·이미지·영수증…", "실행할 때·나중에·매일…" 제외) 연결 자료가 0개면 모델 없이 업로드를 요청합니다. "실행할 때 넣을게요"로 답하면 기존 모델 설계로 이어집니다. `A4B_ATTACHMENT_PRECHECK=off`로 끌 수 있습니다. 문장 판단 부분은 나중에 학습한 경량 분류기로 바꿀 수 있도록 함수로 분리했습니다.
- 확인: "올려준 영수증 이미지 파일 내용을 엑셀파일로 정리해줘"의 설계 처리 0.04초(전체 작업 4.1초), 일반 요청은 기존처럼 모델 경로(약 31초, 모델 상주 상태). 테스트 13개 추가, Python 810개 통과·실패 5개(기존 항목).
- 남은 지연: 세션 생성 약 4.6초와 메시지 약 8.3초의 대부분은 Python 작업 프로세스 시작(호출마다 약 3.5~4초, 메시지 1건에 2회)입니다. 작업 프로세스 재사용은 코드 수정이 필요해 진행하지 않았습니다.

### 10-2. Python 작업 시작 시간(지연 불러오기)

- 원인: Python 자체 시작은 0.1초이고, 작업마다 deepagents(그중 미사용 Anthropic SDK 약 1.4초)·langchain_openai(약 0.6초)·langgraph를 불러오느라 import가 약 3.3초 걸렸습니다.
- 변경: `infrastructure/llm/deep_agents.py`에 `create_deep_agent` 지연 불러오기 함수를 두고 설계·실행·응대 검수 4곳이 이를 사용하게 했습니다. `model_factory.ChatOpenAI`도 첫 사용 때 불러옵니다. 기존 테스트가 바꿔 끼우는 이름은 그대로입니다.
- 결과: import 약 3.3초 → 1.27초(실측 1.5초, 남은 대부분은 모든 설계 작업에 필요한 langgraph). 서버 기준 "올려준 영수증…" 요청 약 13초 → 5.2초, 일반 요청 약 35초 → 21초.
- 테스트: Python 814개 통과·실패 1개(CRLF CSV, 기존). 강제 종료 SQLite 테스트 4개는 이번 실행에서 통과했지만 원래 불안정한 항목이라 해결로 보지 않습니다. Node 703/704 통과.
- 남은 개선: Python 작업 프로세스 재사용(세션 생성 1.5초, 메시지당 프로세스 2회 시작).

### 10-3. 강제 종료 후 DB 열기 재시도

- 원인: Windows에서 강제 종료된 작업 프로세스가 SQLite `-shm` 파일 잠금을 잠깐 더 유지해, 직후 `PRAGMA journal_mode=WAL`이 `disk I/O error`로 실패했습니다(종료 직후 열기 16회 중 6회 실패, 1초 대기 시 16회 모두 통과).
- 변경: `infrastructure/persistence/storage.py`의 `enable_wal`이 기존 BUSY/LOCKED 재시도에 더해 I/O 오류를 최대 2초까지 0.05초 간격으로 재시도합니다. 2초가 지나도 계속되면 실제 디스크 문제로 보고 그대로 오류를 냅니다.
- 확인: 강제 종료 테스트 24회 모두 통과, 재시도·포기 단위 테스트 2개 추가. Python 816개 통과·실패 1개(CRLF CSV, 기존), Node 703/704 통과.

## 11. Docker 일괄 실행 — 2026-11-01

- 구성: 앱·Node 서버·설계용 Python·문서 분석 Python은 Docker, Ollama는 PC에 직접 설치(GPU 직접 사용). 컨테이너는 `host.docker.internal:11434`로 PC의 Ollama에 연결합니다(Docker Desktop에서 Ollama 설정 변경 없이 연결 확인).
- 변경: `Dockerfile`에 문서 분석 환경(`.venv-document-analysis`)과 일괄 검증 스크립트 추가, `docker-compose.yml` 신규(포트 127.0.0.1:3030, 데이터 볼륨, Ollama 주소·모델·검증 defer), `scripts/prepare-ollama.ps1`·`.sh` 신규(Ollama 환경변수 설정·모델 다운로드), README "Docker로 실행" 절 추가.
- `.env`·`.env.example`의 `A4B_DOCUMENT_PYTHON`(macOS 경로 고정)을 주석 처리했습니다. 비워 두면 서버가 운영체제별 경로(`bin/python`·`Scripts\python.exe`)를 찾습니다. 직접 실행(비 Docker) 환경에는 여전히 문서 분석 환경 설치가 필요합니다.
- 실제 확인(이 PC, Docker Desktop 29.9.2): 이미지 빌드 성공(1.85GB), 컨테이너 기동, 업로드 CSV·Excel 정상(표 인식), 글자 있는 PDF 텍스트 추출(엔진 미설치로 partial 표시), JPG는 OCR 없음으로 글자 미추출, "올려준 영수증…" 즉시 안내 3.4초, CSV 첨부 설계는 `read_table → aggregate_table → write_result` 준비 완료(94.8초, 첫 초안 검증 실패 후 자동 수정으로 모델 생성 2회), 컨테이너 안 일괄 검증 실행 성공.
- 미확인: macOS·Linux에서의 실행, OCR 엔진 컨테이너.

### 11-1. Docker 이미지에 OCR(PaddleOCR CPU) 추가

- 배경: 저장소에는 파서 호출·정리 코드만 있고 엔진·모델은 없습니다(팀원 macOS·RunPod에 별도 설치, 전달 압축본은 이 PC에 해시 파일만 존재).
- 변경: `Backend/requirements/paddle-cpu.txt`(paddlepaddle 3.2.2, paddleocr 3.7.0, paddlex 3.7.2) 신규. `Dockerfile`이 `.venv-paddle`을 만들고 코드가 요구하는 모델 3종(`PP-DocLayout_plus-L`, `PP-OCRv5_mobile_det`, `korean_PP-OCRv5_mobile_rec`)을 빌드 때 받아 `A4B_PADDLE_MODEL_DIR`에 둡니다. OpenCV·OpenMP 시스템 라이브러리 추가.
- 버전 선택: 팀 벤치마크의 paddlepaddle 3.3.1은 CPU 추론에서 oneDNN 오류(`ConvertPirAttribute2RuntimeAttribute not support`)가 나서 3.2.2를 사용했습니다.
- 확인: 영수증 사진 업로드 18.7초, 상태 succeeded, 약 760자 추출(상품·단가·수량·합계 포함, 일부 글자 오인식). 이미지 4.24GB.
- 남은 문제: 같은 영수증에 "엑셀파일로 정리해줘" 설계는 `read_document → write_result`로 잘못 연결돼 검증 실패(자동 수정 후에도 실패). 올바른 흐름은 `read_document → extract_information → export_result`이며, 설계 모델 품질 문제입니다. 출력 형식은 현재 CSV·JSON만 지원합니다.
- 미포함: MinerU·Docling(표 구조·스캔 PDF·HWP 변환).

### 11-2. 영수증 설계 실패 원인과 조치

- 원인 1: 설계 지시문에 "문서·사진 → 항목 추출 → 표 파일" 경로가 없어 Qwen이 엑셀=표로 보고 `read_document → write_result`로 연결했습니다. 조치: `conversation.py`·`direct_conversation.py` 지시문에 `read_document → extract_information(fields) → export_result(format=csv)` 규칙, xlsx 미지원(CSV 저장), 없는 노드 연결 금지를 추가했습니다.
- 원인 2: 검증 문구가 모호해 자동 수정이 같은 초안을 반복했습니다. 조치: `workflow.py`가 잘못된 연결의 양쪽(앞 노드가 실제로 내보내는 자료, 필요한 자료, 없는 노드 id)과 맞는 도구(`extract_information → export_result`, `export_result(records)`)를 문구에 포함합니다. 테스트 1개 추가.
- 원인 3(새로 확인): OCR로 파일에 글자가 생기자 Qwen이 첨부 파일 확인(`inspect_resource`)만 하고 `list_tools`를 부르지 않아, 기본 `A4B_TOOL_CATALOG_MODE=model`에서는 3회 모두 "도구 목록 확인이 완료되지 않아" 오류로 중단됐습니다(규칙을 빼도 동일해 지시문 변경과 무관). 도구 목록은 이미 설계 입력에 들어 있으므로 Docker 기본값을 `direct`로 바꿨습니다.
- 결과(Docker, 같은 영수증 3회씩): `model` 모드 3회 모두 중단 → `direct` 모드 중단 없음. 하지만 3회 중 저장 가능한 초안은 0회입니다(질문 2회는 내용이 부정확, 1회는 구체 오류 문구를 받고도 `read_document → filter_sort`로 잘못 연결). 초안 이름에 도구 ID가 들어가는 문제도 반복됩니다.
- 판단: 지시문·문구 보강만으로는 Qwen 8B의 이 유형 설계가 안정되지 않습니다. 코드로 고정하는 방식(문서→표 요청의 결정적 템플릿 또는 잘못된 문서→표 연결의 자동 교정)이 다음 후보입니다.
- 테스트: Python 817개 통과·실패 1개(CRLF CSV, 기존).

### 11-3. 문서 목록 추출과 고정 흐름(영수증 → 표)

- 목록 추출: `extract_information`에 `extraction_mode='rows'`를 추가했습니다. 반복 항목 하나를 한 줄(record에 `row` 번호)로 반환하고, 각 값은 원문 인용 안에 있고 인용이 원문 구간에 있을 때만 기록합니다(OCR 줄바꿈 차이만 허용, 기존 항목 모드는 정확 일치 그대로). 원문에서 하나도 확인되지 않은 줄은 버립니다. 모델 계약(`NodeSettings.extraction_mode`)과 출력 스키마(`Rows`)를 추가했습니다.
- 저장: `export_result`가 줄 단위 결과를 받으면 `file_name,row,<요청 항목>,status,evidence` 열의 CSV를 만듭니다(엑셀에서 바로 열리는 UTF-8 BOM).
- 고정 흐름: `agent_builder/design/document_rows_template.py`. 문서·사진만 첨부되고 요청에 엑셀·표·CSV 등이 있으면 모델 없이 "어떤 항목을 한 줄씩 뽑을까요?"를 묻고, 답한 항목으로 `read_document → extract_information(rows) → export_result(csv)`를 코드로 구성합니다. `A4B_DOCUMENT_ROWS_TEMPLATE=off`로 끌 수 있습니다. 표 파일이 있거나 요약 요청이면 기존 모델 설계로 갑니다.
- Docker 버그 수정: `.dockerignore`의 `**/output` 규칙이 `Backend/workflow_engine/executors/output`(저장 도구)까지 이미지에서 제외하고 있었습니다(재구성 때 폴더명을 바꾸며 생긴 문제). 이전 Docker 확인은 설계까지만이라 드러나지 않았고, 저장 단계가 있는 실행은 Docker에서 모두 실패했을 것입니다. 규칙을 `/output`으로 좁히고, 등록 도구 실행기를 모두 불러올 수 없으면 빌드가 실패하도록 Dockerfile에 검사 단계를 추가했습니다(22개 확인).
- 실제 확인(Docker, 같은 영수증): 질문 2.7초 → 항목 답변 후 초안 준비 2.6초 → 저장 2.6초 → 실행 42초 성공, 6개 상품 모두 한 줄씩 추출, CSV 생성. 값은 6줄 중 4줄이 정확하고 2줄(우유의 단가·수량·금액, 무의 수량)은 OCR 순서가 섞인 숫자를 잘못 짝지었습니다. `verified`는 "원문에 있는 값"이라는 뜻이지 "올바른 칸"이라는 뜻이 아닙니다.
- 테스트 4개 추가, Python 821개 통과·실패 1개(CRLF CSV, 기존), Node 703/704.
