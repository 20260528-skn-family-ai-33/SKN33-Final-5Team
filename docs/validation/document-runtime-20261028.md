# Docling·한글·구형 Office 로컬 실행 검증

검증일: 2026-10-28, macOS arm64. 작업 브랜치: `feat/JHD/document-analysis-routing`.

## 완료한 준비

| 구성 | 실제 준비·확인한 환경 |
|---|---|
| 구조 리더 | 기존 `Backend/.venv-document-analysis` 재사용 |
| Docling | `Backend/.venv-docling`, Python 3.13.13, Docling 2.133.0, docling-core 2.99.0 |
| 추론 | PyTorch 2.14.1, CPU 4스레드, OCR 한국어·영어 |
| OCR | EasyOCR 1.7.2, CRAFT·한국어·영어 인식 가중치 로컬 준비 |
| 레이아웃·표 | Docling Heron 레이아웃·TableFormer 가중치를 기존 BMT 캐시에서 프로젝트 `data/pdf-models/docling`으로 복사 |
| HWP/HWPX 변환 | rhwp v0.8.7을 `data/document-runtime/bin/rhwp`에 복사 |
| 구형 Office 변환 | 로컬 런타임에 포함된 LibreOfficeDev 26.8.0.0.alpha0 사용, 실행별 별도 프로필 |
| 서비스 설정 | 루트 `.env`의 문서 라우팅·Docling Python·로컬 모델·변환기 경로 반영 |

Docling의 `pip check`는 통과했다. `.env`의 다른 서비스 설정은 보존했다. 모델 다운로드는 준비 단계에서만 수행했고 문서 처리 시에는 로컬 모델과 오프라인 설정을 사용했다. LibreOffice는 현재 로컬에 있는 개발판으로 검증했으므로 배포 환경의 버전은 따로 고정하고 같은 파일로 재검증해야 한다.

패키지 전체 버전, 모델·실행 파일 SHA256, 실제 경로는 `output/document-runtime-validation/20261028/environment.json`과 `docling-freeze.txt`에 기록했다. 이 파일은 현재 macOS 환경의 설치 기록이며 다른 운영체제용 잠금 파일은 아니다. 모델 가중치·가상환경·원본 문서·결과는 Git에 넣지 않는다.

## 실제 문서 9개

기존 BMT의 작성형 시험 자료를 복사해 사용했다. 원본 파일은 수정하지 않았고 실행 전후 SHA256가 9개 모두 동일했다. 정답은 파서 실행이 끝난 뒤 평가에만 사용했다.

| 입력 | 파일 수 | 변환·기본 처리 | 엔진 실행 성공 | 지정 필드 보존 |
|---|---:|---|---:|---:|
| HWP | 3 | rhwp → PDF → Docling | 3/3 | 13/13 |
| HWPX | 3 | rhwp → PDF → Docling | 3/3 | 13/13 |
| DOC | 1 | LibreOffice → DOCX → 원본 구조 리더 + Docling | 1/1 | 5/5 |
| PPT | 1 | LibreOffice → PPTX → 원본 구조 리더 + Docling | 1/1 | 5/5 |
| XLS | 1 | LibreOffice → XLSX → openpyxl + XML | 1/1 | 3/3 |
| 합계 | 9 | | 9/9 | 39/39 |

여기서 성공은 필요한 변환기·기본 엔진이 실제로 실행됐다는 뜻이다. DOC/PPT는 원본 구조 추출과 별도로 Docling 출력에도 지정 필드가 모두 있었다. XLS는 설계에 따라 신경망 파서를 호출하지 않는다. XLS에 실제로 없는 주민번호·은행명은 평가에서 제외했다.

한글 6개 파일은 본문·표·다단 문서 **3종을 HWP/HWPX로 각각 저장한 자료**다. 6개의 독립된 문서 유형으로 계산하지 않는다. 전체 문서 정확도, 운영 자료에 대한 일반화 성능, 처리 속도 순위는 이번 실행으로 측정하지 않았다.

### 구조·표기 확인

| 확인 항목 | 결과 | 해석 |
|---|---|---|
| 지정 필드 문자열 | 39/39 완전 일치 | 해당 자료에서 정한 필드만 검사 |
| 한글 표 셀 | 26/32 완전 일치, 공백 제거 후 32/32 | 행·열 위치를 맞춰 비교. 6개 셀의 공백 차이는 원시 출력에 그대로 남음 |
| 한글 표 병합 | 4/4 | HWP·HWPX 각 2개 병합 범위 |
| 다단 읽기 순서 | 지정 앵커 순서쌍 12/12 | 한글 두 파일에서 각각 6쌍. 문서 전체 순서 정확도와 다름 |
| 체크박스 포함 표기 | 완전 일치 0/4, 공백 제거 후 4/4 | `[V]` 계열의 공백 차이. 독립적인 체크 상태 판정은 검증하지 않음 |
| 서명 표기 문자열 | 4/4 | 인장·필기 서명의 시각 인식 검증은 아님 |
| XLS 병합·숫자·빈칸 | 병합 2/2, 지정 숫자 셀 일치, 빈칸 예시 4/4 | 변환본의 native 셀 값과 구조 검사 |

9개 모두 `partial` 상태를 유지했다. 변환본의 원본 보존 검토가 필요하고, 한글 표에는 중요 값의 공백 차이가 있으며, PPT 발표자 메모는 현재 추출 범위에 포함되지 않는다. 실행 성공을 근거로 완료 상태로 올리지 않았다.

실제 서비스의 `parseBuilderAsset` 업로드 함수도 DOC·XLS·HWPX 표 문서 **3/3**으로 확인했다. Python 결과가 TypeScript 계약 검증을 통과하고 원본 해시·단위·블록·실행 경로·검토 상태가 업로드 자산에 전달됐다. 별도 결과 폴더를 사용했으며 업무 DB에는 등록하지 않았다.

## 스캔 OCR와 발견한 문제

같은 한글 표 페이지를 래스터 PNG와 텍스트 레이어 없는 스캔 PDF로 만들어 Docling OCR을 실행했다. 원래 한글 문서에서 파생한 추가 입력으로, 독립된 정답 문서 수에 더하지 않았다.

1. **DPI 보존**: 2배 렌더한 PNG에는 144 DPI가 필요했지만 초기 시험 파일은 PyMuPDF 기본 96 DPI 메타데이터가 남았다. 두 PNG의 디코딩된 픽셀 SHA256는 같았다. 이 잘못된 PNG에서는 15개 셀만 나왔고, 올바른 DPI의 전체 PNG에서는 16/16 셀·2/2 병합·4/4 필드가 맞았다. 스캔 PDF도 16/16 셀·2/2 병합·4/4 필드를 보존했다. 이미지 정규화·EXIF 회전·크롭은 유효한 원본 DPI를 유지하고, PDF 렌더는 배율에 맞는 DPI를 기록하도록 수정했다. DPI가 없는 이미지의 물리 크기를 추정하지는 않는다.
2. **표 크롭 문맥**: 실제 Paddle 레이아웃 탐지 → Paddle OCR/Docling 경로에서 표 크롭은 문자를 읽었지만 셀 구조를 만들지 못했다. 이 경우에만 해당 이미지 전체를 Docling으로 한 번 더 대조하도록 보완했다. 수정 후 실제 4개 엔진 호출(레이아웃·문자 OCR·표 크롭·전체 이미지 대조)이 모두 성공했고, 대조 후보에서 16/16 셀·2/2 병합을 확보했다.

전체 이미지 대조는 기존 보완 횟수 제한 안에서 이미지당 한 번만 실행한다. 결과는 `phase=comparison`, `selected=false`로 보존하며 원래 크롭 결과·좌표·실패 사유를 유지한다. 이번 이미지 사례의 기본 `tables` 뷰는 비어 있고, 구조는 `document_analysis.blocks`의 대조 후보에 있다. 자동 채택이나 검토 완료 처리를 하지 않았다. 레이아웃 모델의 중복 텍스트 탐지도 관찰되어 읽기 순서·중복 제거 품질은 추가 BMT 대상이다.

## 검증 기록과 재실행

아래 경로는 저장소 루트 기준이다. 입력 사본·변환본·엔진 로그·원시 출력도 각 작업 폴더에 함께 남겼다.

- 입력·정답 연결 및 해시: `output/document-runtime-validation/20261028/cases.json`
- 9개 파일 최종 실제 실행: `output/document-runtime-validation/20261028/final-runs/20261028-120536/7f7b5489/summary.json`
- 같은 실행의 목록: 위 폴더의 `execution.csv`
- 서비스 업로드 함수: `output/document-runtime-validation/20261028/service-upload-summary.json`
- PNG 픽셀·DPI 비교: `output/document-runtime-validation/20261028/ocr-probes/dpi-metadata-comparison.json`
- 전체 PNG의 Docling 평가: `output/document-runtime-validation/20261028/ocr-probes/image-144dpi-evaluation.json`
- 스캔 PDF 및 초기 PNG 평가: `output/document-runtime-validation/20261028/ocr-probes/evaluated-summary.json`
- 실제 이미지 경로·문맥 대조: `output/document-runtime-validation/20261028/ocr-probes/pipeline-context-comparison/summary.json`
- 환경·모델 해시: `output/document-runtime-validation/20261028/environment.json`

```sh
Backend/.venv-document-analysis/bin/python Backend/scripts/verify_document_runtime.py \
  --cases output/document-runtime-validation/20261028/cases.json \
  --settings output/document-runtime-validation/20261028/runtime-settings.json \
  --output output/document-runtime-validation/20261028/reruns
```

매 실행은 날짜·UUID 폴더에 저장되어 앞선 결과를 덮어쓰지 않는다. 정답 경로는 이 작업 공간의 BMT 자료를 가리킨다. `--prior-report`를 쓰면 기존 엔진 결과를 재평가하고 `inference_reexecuted=false`로 기록한다. 초기 환경 경로 오류로 실패한 실행과 DPI 수정 전 출력도 삭제하지 않았다.

수정 후 Python 단위 검사 **18개**, 관련 TypeScript 회귀 검사 **9개**, TypeScript 타입 검사, Python 문법 검사, 설치 스크립트 구문 검사, `git diff --check`를 통과했다. Python 단위 검사의 신경망은 테스트 더블이며, 위 실제 엔진 실행 결과와 구분한다.
