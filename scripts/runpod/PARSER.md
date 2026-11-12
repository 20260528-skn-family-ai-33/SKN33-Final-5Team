# 통합 파서 소스와 배포

`Backend/documents/analysis`, `Backend/documents/parsers`와 아래 RunPod 모듈은
같은 Git 커밋에서 배포한다. 운영본의 파일을 개별 수정해 별도 버전으로 관리하지 않는다.

- `gpu_parser_server.py`: 이미지 layout/OCR와 비동기 PDF HTTP 경로
- `pdf_jobs.py`: PDF 작업 접수, 직렬 실행, 결과 보관
- `pod_pdf_worker.py`: MinerU/Docling 라우터 실행과 결과 ZIP 생성
- `setup-pdf-router.sh`: 기존 CUDA 환경을 유지하면서 Docling 환경 준비
- `parser-paths.sh`: 설치와 supervisor가 공유하는 경로 기본값
- `parser_readiness.py`: PDF worker 실행 파일·모델 경로 확인

앱의 `analyze_document.py`는 `A4B_PARSER_URL`이 있으면 PDF 원본을 서버로 보낸다.
서버의 PDF worker는 같은 소스의 `pipeline.run(..., pdf_primary=...)`를 직접 호출한다.
PDF 내부의 이미지 보완은 같은 팟의 layout/PaddleOCR 서버로 요청한다.

## 환경별 설정

| 변수 | 용도 |
| --- | --- |
| `A4B_PARSER_URL`, `A4B_PARSER_API_KEY` | 앱이 사용하는 파서 주소와 키 |
| `A4B_PARSER_PORT`, `A4B_PARSER_BIND` | 서버 포트(기본 8100), 바인드 주소(기본 0.0.0.0) |
| `A4B_BASE_DIR` | 네트워크 볼륨의 데이터·환경 루트. 기본 `/workspace/a4b` |
| `A4B_PARSER_SOURCE_ROOT` | 서버·worker 소스 루트. 기본은 시작 스크립트가 있는 Git 체크아웃의 루트 |
| `A4B_PDF_VENV`, `A4B_PDF_MODELS` | 설치 위치. 기본 `$A4B_BASE_DIR/envs/pdf`, `$A4B_BASE_DIR/models/pdf` |
| `A4B_PDF_ROUTER_PYTHON` | PDF worker Python. 기본 `$A4B_PDF_VENV/bin/python` |
| `A4B_MINERU_PYTHON` | 설정되어 있으면 Pod의 기본 PDF 엔진은 MinerU, 없으면 Docling |
| `A4B_PDF_ARTIFACTS_DIR`, `A4B_PDF_OCR_MODEL_DIR` | 기본 `$A4B_PDF_MODELS/docling`, 그 아래 `EasyOcr` |
| `A4B_POD_PADDLE_URL` | PDF 보완용 이미지 엔진 주소. 기본은 로컬 서버 포트 |
| `A4B_API_AUTH_REQUIRED` | 기본 인증 사용. 명시적으로 0이면 기존 Pod의 인증 우회 정책 유지 |

소스 통합 자체는 운영 인증 정책, 모델, 패키지 버전, 저장 경로를 변경하지 않는다.
키 값은 Git이나 배포 manifest에 포함하지 않는다.

`setup-pdf-router.sh`와 `start-final.sh`는 같은 `parser-paths.sh`를 사용한다.
새 환경의 설치 위치는 `A4B_PDF_VENV`·`A4B_PDF_MODELS`로 지정하고 시작 시에도
동일하게 전달한다. 준비 스크립트는 Python·모델의 하위 경로가 서로 충돌하면 설치 전에 실패한다.
`/health`의 `pdf_jobs`와 `pdf_readiness.ready`는 실행 가능한 Python, worker 파일,
비어 있지 않은 Docling/OCR 폴더를 모두 요구한다. 설정된 MinerU Python도 검사한다.
준비되지 않은 PDF 요청은 503으로 거절하며, 이미지 엔진 상태는 별도로 유지한다.
supervisor는 이 검사와 실행 중 서버의 소스 루트까지 확인한 후 게이트웨이를 연다.
이 검사는 파일 경로에 대한 검사이며 모델의 CUDA 실행 여부는 실제 PDF 호출로 검증한다.

## 배포 확인 순서

1. `/workspace/a4b/backups/parser/<작업시각>/`에 현재 소스와 시작 설정을 백업하고 해시를 검증한다.
   실행 환경 백업에 키가 포함된다면 접근 권한을 600으로 제한하고 서버 안에만 보관한다.
2. 로컬 문서 테스트와 `Backend/tests/deployment/test_runpod_parser*.py`를 실행한다.
   Linux에서는 `test_runpod_supervisor.py`로 재시작·소스 선택·누락 시 중단도 검사한다.
3. 확정한 Git 커밋의 소스를 `/workspace/a4b/parser-releases/<커밋>/`에 배치한다.
   manifest에 커밋과 각 소스의 SHA-256을 기록해 로컬·압축본·배포본을 대조한다.
4. 기존 환경과 모델을 사용해 임시 루프백 포트에서 OCR·PDF 실제 처리를 확인한다.
5. 진행 중인 PDF 작업이 없는지 확인한 다음 `/post_start.sh`의 시작 명령을
   `bash /workspace/a4b/parser-releases/<커밋>/scripts/runpod/start-final.sh`로 전환한다.
   소스와 시작 스크립트를 함께 커밋별로 배포하며, 기본 경로는 그 배포본으로 결정된다.
   시작 파일을 별도 위치에 유지한다면 `A4B_PARSER_SOURCE_ROOT`로 확정한 배포본을 명시한다.
   `legacy-pdf` 경로나 수동 심볼릭 링크는 시작 경로 선택에 사용하지 않는다.
   현재 supervisor는 파서 종료 시 Ollama와 게이트웨이도 다시 시작하므로,
   운영 전환은 해당 서비스 영향까지 확인한 뒤 수행한다.
6. 기존 8100 주소로 앱 연결, PDF·OCR, 소스 해시와 `pdf_readiness.source_root`를 확인한다.
   실패 시 백업한 시작 명령·소스 경로로 복원한다.

## PDF 요청 제한과 작업 종료

이미지 OCR/layout 요청은 최대 32장 및 JSON/base64 인코딩 후 48 MiB에 맞춰 나눈다.
이미지 한 장의 12 MiB 제한은 유지하며, 배치 응답을 원래 페이지 이름으로 합친다.
한 배치라도 실패하면 필수 OCR 성공으로 기록하지 않는다.

PDF 클라이언트는 접수 시 남은 제한 시간을 `X-A4B-PDF-Timeout` 헤더로 전달한다.
헤더가 없는 기존 클라이언트는 서버 기본 290초를 사용하고, 명시한 값도 서버 상한
1,500초 이내로 제한한다. 대기열 재시도 시간은 클라이언트의 전체 제한 시간에 포함된다.
결과 조회는 90초 임대를 갱신하지만 전체 실행 제한을 연장하지 않는다.
클라이언트는 오류·시간 초과 시 인증된 `DELETE /v1/pdf/<job_id>`로 취소한다.
클라이언트 강제 종료나 접수 응답 유실로 취소하지 못해도 임대 만료가 작업을 정리한다.
서버는 worker와 같은 프로세스 그룹의 엔진 자식들을 종료한 후 다음 PDF를 접수한다.
취소·임대 만료·실행 제한 결과는 각각 `pdf_job_cancelled`, `pdf_job_lease_expired`,
`pdf_job_timeout` 코드와 HTTP 410으로 반환한다. 완료 결과는 기존처럼 300초 보관한다.
보관 한도 8건에 도달하면 조회가 끝난 결과 또는 취소·만료·실패 기록 중 가장 오래된
항목을 제거하고 새 작업을 받는다. 오류 기록은 GET 조회 여부와 관계없이 제거할 수
있으며, 제거된 작업 ID 조회는 404를 반환한다. 아직 조회하지 않은 정상 ZIP 결과는
보호한다. 미조회 정상 결과만 8건인 경우에는 기존처럼 조회 또는 보관 만료까지
접수를 429로 제한한다. 실행 중인 worker는 보관 용량 확보 대상으로 제거하지 않는다.

이번 변경은 통합과 리뷰에서 지적한 동작 오류 수정 범위다. Docling 비교 정책의
페이지 간 본문 중복, 모델 상주화 등 품질·성능 개선은 포함하지 않는다.
