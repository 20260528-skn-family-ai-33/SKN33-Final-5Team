# MinerU·PaddleOCR 기본 설정과 실제 HTTP 업로드 검증

검증일: 2026-10-28. 브랜치: `feat/JHD/document-analysis-routing`.

## 기본 실행 환경 연결

루트 `.env`에 아래 설정을 연결했다. 기존의 다른 서비스 설정과 Docling·rhwp·LibreOffice 설정은 보존했다. 실제 `.env`는 Git에서 제외하며 파일 권한은 600이다.

| 대상 | 연결 내용 |
|---|---|
| MinerU Python | 기존 BMT 가상환경의 Python 3.13.13·MinerU 4.0.12·PyTorch 2.14.1 |
| MinerU 모델 | `MINERU_HOME=data/document-runtime/mineru`의 절대 경로, 소형 모델 backend `torch`, 로컬 VLM 엔진 `llama-cpp` |
| Paddle Python | 기존 BMT 가상환경의 Python 3.13.13·PaddleOCR 3.7.0·PaddlePaddle 3.3.1·PaddleX 3.7.2 |
| Paddle 모델 | `data/document-runtime/paddlex/official_models` 아래 `PP-DocLayout_plus-L`, `PP-OCRv5_mobile_det`, `korean_PP-OCRv5_mobile_rec` |
| Paddle 장치 | CPU |
| 캐시 | `PADDLE_PDX_CACHE_HOME`, `HF_HOME`을 프로젝트 `data/document-runtime` 아래 쓰기 가능한 경로로 지정 |

가중치·설정 파일 36개, 약 2.28GB를 기존 로컬 캐시에서 복사했다. 복사본은 심볼릭 링크가 아닌 실파일이며 새 모델 다운로드는 없었다. Python 가상환경은 기존 경로를 재사용하므로 해당 환경을 이동·삭제하면 `.env`의 실행 파일 경로도 갱신해야 한다. Python 실행 파일 경로는 가상환경 경로를 유지하며 심볼릭 링크의 최종 대상 경로로 바꾸지 않았다.

모델 파일별 크기·SHA256와 연결한 파서 설정만 `output/document-service-upload-validation/20261028/environment-connection.json`에 기록했다. 패키지 버전은 같은 폴더의 `environment-versions.json`에 기록했다. 서비스 파서 워커는 기존과 같이 로컬 모델·오프라인 설정을 사용한다.

## 실제 서비스 검증 방법

`Frontend/scripts/verify-document-upload.ts`로 빌드된 실제 서버를 `scripts/run-configured.mjs`에서 시작했다. 서버는 기본 루트 `.env`를 읽고, 시험 실행에서는 루프백 포트·시험 데이터 디렉터리·데모 모드만 별도로 지정했다. 파서 Python이나 모델 경로는 시험 코드에서 덮어쓰지 않았다.

검증은 다음 실제 HTTP API를 통과했다.

1. `POST /api/agent-builder/sessions`: 대화 생성
2. `POST /api/agent-builder/upload`: 파일 전달 → 실제 파서 실행 → 자산·분석 JSON을 시험 SQLite에 저장
3. `GET /api/agent-builder/original`: 저장한 원본을 내려받아 SHA256 비교
4. 서비스 종료·재시작
5. `GET /api/agent-builder/session`: 저장된 자산·분석 메타데이터·부분 처리 상태 재조회

새 시험 DB와 결과 폴더를 사용해 기존 업무 데이터와 분리했다. 시험 종료 시 서버도 종료했다. 브라우저 UI 조작이나 업무용 LLM 질의는 이번 검증 범위에 넣지 않았다. 파서 엔진은 테스트 더블로 대체하지 않았다.

## 결과

| 시험 입력 | 실제 실행 경로 | 지정 문자열 보존 | 서비스 상태 |
|---|---|---:|---|
| 텍스트 PDF | MinerU 기본 처리 | 2/2 | `succeeded` / `ready` |
| 문자 PNG | LayoutDetection → PaddleOCR | 4/4 | `succeeded` / `ready` |
| 한글 표 PNG | LayoutDetection → PaddleOCR + Docling 표 크롭 → Docling 이미지 전체 대조 | 4/4 | `partial` / `partial` |

- HTTP 업로드 검증 **3/3 통과**, 지정 문자열 **10/10 보존**.
- PDF는 MinerU 4.0.12의 기본 호출이 성공했다. Docling 대체 처리만 성공한 경우는 통과로 계산하지 않는다.
- 이미지의 레이아웃·문자 OCR과 표 크롭·문맥 대조 호출이 모두 성공했다.
- 한글 표의 대조 후보는 **16/16 셀의 내용·행열 위치·병합 일치**, 병합 범위 **2/2 일치**.
- 이 표 크롭의 셀 구조는 여전히 누락되어 `table_structure_missing` 검토 사유를 유지한다. 전체 이미지 결과는 `phase=comparison`, `selected=false` 후보이며, 기본 `tables`에는 자동 채택하지 않았다.
- 원본 다운로드와 입력 사본의 해시가 **3/3 일치**했고, 원래 시험 원본 파일도 보존됐다.
- 서비스 재시작 후 **3개 자산의 분석 JSON과 parser 상태가 모두 동일**했다.

PDF·영수증 문자는 합성 시험 입력이며 한글 표 이미지는 이전 한글 작성형 시험 문서에서 파생했다. 이 3건은 기본 서비스 연결과 경로·저장 검증이며, 전체 PDF·이미지 정확도나 운영 문서 holdout BMT를 의미하지 않는다.

## HTTP 검증 중 수정한 초기화 문제

최초 실행에서 새 대화 생성이 실패했다. 진단 결과는 `Storage.__init__`의 WAL 설정에서 발생한 SQLite `OperationalError`였고, 8개 워커의 동시 신규 DB 초기화에서도 `database is locked`를 재현했다. 서비스 시작 시 예약 검사 워커와 요청 워커가 동시에 접속할 수 있었다.

`PRAGMA journal_mode=WAL`에서 BUSY/LOCKED가 반환되는 경우에만 최대 10초 범위에서 재시도하도록 보완했다. 그 외 오류는 그대로 반환하며 초기화 실패 연결은 닫는다. 실제 SQLite 쓰기 잠금·병렬 초기화 회귀 검사 2개를 추가했다. 보완 후 새 DB로 최종 HTTP 검증을 다시 수행해 위 3건과 서비스 재시작을 모두 통과했다. 첫 실패 실행도 삭제하지 않았다.

관련 Backend 검사 **66개**, TypeScript 파서 검사 **9개**, 대화·검토 API와 예약 워커 검사 **5개**, 타입 검사·검증 도구 타입 검사·프런트엔드 빌드가 통과했다.

## 기록과 재실행

저장소 루트 기준:

- 시험 입력·정답·필수 엔진 경로: `output/document-service-upload-validation/20261028/cases.json`
- 최종 실제 실행: `output/document-service-upload-validation/20261028/final-runs/2026-10-28T03-28-48-501Z-54de6760/summary.json`
- 같은 폴더의 `*-asset.json`: 업로드 응답에서 저장한 자산
- 같은 폴더의 `service-data/`: 시험 DB·원본·변환/크롭·엔진 원시 출력과 로그
- 같은 폴더의 `server-1.log`, `server-2.log`: 서비스 시작·재시작 기록
- 소스 보존·검사·검증 코드 해시: `output/document-service-upload-validation/20261028/final-summary.json`

```sh
cd Frontend
npm run build
node --import tsx scripts/verify-document-upload.ts \
  --cases ../output/document-service-upload-validation/20261028/cases.json \
  --output ../output/document-service-upload-validation/20261028/reruns
```

매 실행은 날짜·UUID 폴더를 사용한다. macOS에서는 MinerU 로컬 추론과 루프백 서버를 허용하는 실행 환경이 필요하다. `.env`·가상환경·모델·시험 DB·입력·생성 결과는 Git에 포함하지 않는다.
