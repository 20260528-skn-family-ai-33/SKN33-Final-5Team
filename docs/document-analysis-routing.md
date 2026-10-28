# 문서 내용 분석기와 파서 라우팅

2026-10-28 보고서의 기본 경로·내용 분류·영역 보완을 서비스 업로드와 문서 읽기 노드에 연결한다. 기존 63항목 BMT 수치를 새 구현의 성능으로 사용하지 않는다.

## 설치와 실행

저장소 루트에서 `bash Backend/scripts/setup-document-analysis.sh`를 실행한다. 구조 리더 전용 환경이 만들어진다. `.env.example`의 문서 분석 설정을 루트 `.env`에 반영한다. 기존 `.env`는 설치 스크립트가 변경하지 않는다.

신경망 엔진은 별도 환경을 사용한다. Docling은 `bash Backend/scripts/setup-document-demo.sh`로 설치하고 `Backend/.venv-docling/bin/python Backend/scripts/prepare_parser_models.py`로 모델을 준비한 후 `EASYFLOW_DOCLING_PYTHON`, `A4B_PDF_ARTIFACTS_DIR`, `A4B_PDF_OCR_MODEL_DIR`를 루트 `.env`에 설정한다. 설치 스크립트는 기존 가상환경을 재사용하며 새 환경은 Python 3.13을 우선 선택한다. 다른 실행 파일은 `A4B_DOCLING_SETUP_PYTHON`으로 지정한다. 현재 서비스의 Docling OCR 설정은 한국어·영어 EasyOCR이다.

MinerU 어댑터는 4.0의 `MinerUParser(tier='standard')`와 MiddleJson 2.0 계약을 사용한다. `A4B_MINERU_PYTHON`과 쓰기 가능한 `MINERU_HOME`을 설정하고 해당 버전의 Standard 모델을 미리 준비한다. 로컬 small-model/VLM 실행 설정은 MinerU의 환경 설정을 따른다. 원격 API나 자동 다운로드를 사용하지 않는다.

PaddleOCR 3.x 환경을 `A4B_PADDLE_PYTHON`에 설정하고, `A4B_PADDLE_MODEL_DIR` 아래에 다음 세 모델을 준비한다.

- `PP-DocLayout_plus-L`: 종류·영역 좌표 탐지
- `PP-OCRv5_mobile_det`: 문자 위치 탐지
- `korean_PP-OCRv5_mobile_rec`: 한국어 문자 인식

레이아웃 모델 경로만 별도로 지정하려면 `A4B_LAYOUT_MODEL_DIR`를 사용한다. Paddle 장치는 기본 CPU이다. HWP/HWPX에는 `rhwp export-pdf INPUT -o OUTPUT`, 구형 Office에는 LibreOffice `--headless --convert-to`를 사용한다. `A4B_RHWP`와 `A4B_LIBREOFFICE`로 실행 파일을 지정할 수 있다. 모델·변환 도구 설치는 이 코드가 자동 수행하지 않는다.

직접 실행 예:

```sh
Backend/.venv-document-analysis/bin/python Backend/scripts/analyze_document.py input.pdf output/result.json --work-dir output/document-job
```

작업 폴더는 실행마다 새 폴더를 사용한다. 웹 서버는 원본 SHA256와 실행 UUID로 자동 분리한다. 환경 변수의 상대 경로는 프로젝트 루트를 기준으로 사용한다.

## 처리 경로

| 입력 | 구조 조사·기본 처리 | 보완 |
|---|---|---|
| PDF | PyMuPDF 조사 → MinerU 기본 분석·파싱 | 글자가 이미 추출된 영역은 재사용. 텍스트 없는 그림 영역 분류, 구조가 없는 표는 페이지 문맥으로 Docling 대조 |
| HWP/HWPX | rhwp → PDF → Docling | 원본 SHA·변환 SHA·변환 페이지 연결. 이미지 문자 경로 재사용 |
| DOCX/PPTX | OOXML 객체·관계 조사 + python-docx/python-pptx 보조 조사, Docling 결과는 별도 후보 | 삽입 이미지 → 공통 이미지 경로. 원본 본문·셀·객체가 우선 |
| XLSX | 전체 시트의 셀·병합·수식·캐시·표시 형식·그림·차트 조사, openpyxl + 원본 XML | 삽입 이미지 분류 → 문자 PaddleOCR / 표 Docling |
| DOC/PPT/XLS | LibreOffice로 작업 사본 변환 | 변환본에 해당 경로 적용, 보존 검토 상태 기록 |
| CSV/TSV | csv 모듈로 논리 행·원래 필드 보존 | 선행 0·인용 필드 안 줄바꿈 보존, OCR 없음 |
| TXT/MD | 원문 직접 보존 | 외부 이미지 참조는 기록만 하고 가져오지 않음 |
| 이미지·TIFF·HEIC | 방향·프레임 조사 → LayoutDetection | 글자 PaddleOCR / 표 Docling. 크롭에 표 셀이 없으면 해당 이미지 전체로 한 번 대조 / 차트·수식 의미는 검토 |

기존 표 업무용 CSV/XLSX `rows`는 유지한다. XLSX의 표 행 뷰는 기존과 같이 첫 시트를 사용하지만, 분석 JSON은 여러 시트·그림·수식을 보존한다. 분석된 표 파일도 `read_document`로 읽을 수 있다. 원본 셀 값과 OCR 결과는 부모·자식 관계로 공존하며 덮어쓰지 않는다.

원본에 유효한 DPI가 있으면 이미지 정규화·회전·크롭에도 보존한다. PDF에서 만든 이미지는 렌더 배율에 맞는 DPI를 기록한다. DPI가 없는 사진의 물리 크기는 추정하지 않는다. 표 크롭의 구조 추출 실패 시 이미지 전체 대조 결과는 `phase=comparison`, `selected=false` 후보로 남긴다. 이미지당 한 번, 기존 보완 처리 상한 안에서만 실행하며 기본 추출값을 바꾸거나 검토 상태를 해제하지 않는다.

## 결과 계약과 출처

`parser_metadata.document_analysis`에 `a4b.document_analysis.v1` 결과가 저장된다. DB 규격 초안 `a4b.parser_result.v0.1-draft`와 다른 서비스 실행 계약이다. 초안 DB 테이블을 자동 생성하거나 DB 업무 추출 규격을 변경하지 않는다.

JSON Schema는 `Backend/document_analysis/result.schema.json`이다. Python·TypeScript 실행 검증은 ID 중복·부모 순환·처리 단위 참조·전체 크기 제한도 확인한다.

- `source_sha256`, `source_format`, `primary_processor`: 원본과 기본 처리 정책
- `units`, `blocks`: 문서 단위와 원본 객체·후보·영역·내용
- `native_location`: PDF 페이지, 변환 PDF 페이지, Word 본문 인덱스, PPT 슬라이드/객체, Excel 시트/셀, CSV 논리행, 이미지 프레임
- `parent_object_id`, `bbox`, `data.*transform`: 부모 관계와 좌표 변환. 확보하지 못한 원본 좌표는 생성하지 않음
- `routing_decisions`: 형식·영역 유형·실패·표 구조 신호에 따른 규칙 선택 근거
- `routes`: 실제 실행 엔진·버전·모델 설정·입력 SHA·상태·시간·진단 파일 위치
- `coverage`, `review_required`, `review_reasons`: 처리 범위와 미처리·충돌·검토 상태

`text/pages/sections/tables`는 기존 서비스 호환 뷰이다. Office의 `pages`는 논리 처리 단위이며 물리 페이지가 아니다. 새 검색 근거는 블록 ID·원본 위치를 전달한다. HWP 변환 페이지를 원본 HWP 문단 위치로 표시하지 않는다. 원문·표 HTML은 데이터이며 실행하지 않는다.

웹 작업 기록은 `EASYFLOW_DATA_DIR/document-results/<원본SHA>/<실행UUID>/`에 남는다. 원본 사본, 변환본, 추출 이미지, 크롭, 엔진 원시 출력, 응답, 로그를 보존한다. 실패한 호출도 결과 코드와 기록을 남긴다. 이 폴더에는 문서의 개인정보가 포함될 수 있으므로 기존 업로드 자료와 같은 보관·접근 정책을 적용한다. 현재 버전은 파일별 별도 프로세스이며 요청 간 모델 상주 최적화는 후속 작업이다.

## 검토·제한

입력 8MB, 처리 단위 500개, 객체 20,000개, 본문 1,000,000자, 보완 대상 기본 16개(최대32), 분석 JSON 6MB, 서비스 응답12MB 제한을 적용한다. Office 압축 항목2,000개·개별16MB·전체64MB 제한을 적용한다. 초과 범위는 조용히 버리지 않는다.

기본 엔진이 실패하면 PDF는 Docling으로 대조하고, 처리 가능한 원본 텍스트가 있으면 부분 결과로 보존한다. 이미지 모델이 없으면 원본 이미지와 실패 경로를 남기고 검토 필요로 반환한다. 부분 처리를 완료 상태로 바꾸지 않는다. 이미지만 있는 자료는 등록 가능하지만 텍스트 검색에 사용할 수 있는 것은 추출된 본문뿐이다.

탐지 점수는 문서 정확도가 아니다. 분기 예시의30자·5%·60%, 탐지 임계값0.5는 초기 정책이다. 원본 중요 숫자 누락, 표 구조 부재, 누락 응답, 미처리 객체·프레임은 검토 신호이다. 체크박스 의미 판정·독립 서명 인식·이미지 차트/수식 의미 해석·레이아웃 읽기 순서의 정확도 보장은 아직 제공하지 않는다. Word 보조 영역과 PPT 발표자 메모 등 미처리 범위는 경고에 남긴다.

## 검증

```sh
Backend/.venv-document-analysis/bin/python -m unittest Backend.document_analysis.test_pipeline Backend.document_analysis.test_resources -v
cd Frontend
npm run check
node --import tsx --test server/document-analysis.test.ts server/builder-documents.test.ts server/agent-builder.test.ts
npm run build
```

분기 단위 테스트는 신경망 엔진을 테스트 더블로 대체하고 실제 PDF·Office·이미지 파일로 원본 보존·계약·좌표·실패 상태를 검사한다. 이를 OCR 성능 BMT라고 부르지 않는다. 실제 엔진 스모크 테스트와 한국어 혼합 문서 BMT는 별도이며, 엔진·모델 버전과 초기화·처리 조건을 기록해야 한다. `A4B_DOCUMENT_ROUTING=0`으로 기존 업로드·Docling 문서 경로를 선택할 수 있다.

2026-10-28 로컬 실행 환경 준비 및 실제 HWP/HWPX·구형 Office 9개 파일, 서비스 업로드 함수, 스캔 OCR의 결과와 한계는 [실행 환경 검증 기록](validation/document-runtime-20261028.md)에 정리했다. 재실행 도구는 `Backend/scripts/verify_document_runtime.py`이며 정답은 모델 실행이 끝난 뒤 평가에만 사용한다. 엔진 실행 성공 수와 필드·표 구조 보존 결과를 따로 기록한다.
