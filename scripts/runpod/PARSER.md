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
| `A4B_PDF_VENV`, `A4B_PDF_MODELS` | 실행 환경은 로컬 `/opt/a4b-envs/pdf`, 모델은 `$A4B_BASE_DIR/models/pdf` |
| `A4B_PDF_ROUTER_PYTHON` | PDF worker Python. 기본 `$A4B_PDF_VENV/bin/python` |
| `EASYFLOW_DOCLING_PYTHON` | Docling 자식 프로세스 Python. 기본 `$A4B_PDF_VENV/bin/python` |
| `A4B_MINERU_PYTHON` | 설정되어 있으면 Pod의 기본 PDF 엔진은 MinerU, 없으면 Docling |
| `A4B_PDF_ARTIFACTS_DIR`, `A4B_PDF_OCR_MODEL_DIR` | 기본 `$A4B_PDF_MODELS/docling`, 그 아래 `EasyOcr` |
| `A4B_POD_PADDLE_URL` | PDF 보완용 이미지 엔진 주소. 기본은 로컬 서버 포트 |
| `A4B_API_AUTH_REQUIRED` | 기본 인증 사용. 명시적으로 0이면 기존 Pod의 인증 우회 정책 유지 |

Docling 런타임은 로컬 디스크에서 실행한다. 운영 인증 정책, 모델, 패키지 버전은 유지한다.
키 값은 Git이나 배포 manifest에 포함하지 않는다.

`setup-pdf-router.sh`와 `start-final.sh`는 같은 `parser-paths.sh`를 사용한다.
새 환경의 설치 위치는 `A4B_PDF_VENV`·`A4B_PDF_MODELS`로 지정하고 시작 시에도
동일하게 전달한다. 준비 스크립트는 Python·모델의 하위 경로가 서로 충돌하면 설치 전에 실패한다.
`/health`의 `pdf_jobs`와 `pdf_readiness.ready`는 실행 가능한 Python, worker 파일,
비어 있지 않은 Docling/OCR 폴더를 모두 요구한다. 설정된 MinerU Python도 검사한다.
준비되지 않은 PDF 요청은 503으로 거절하며, 이미지 엔진 상태는 별도로 유지한다.
supervisor는 이 검사와 실행 중 서버의 소스 루트까지 확인한 후 게이트웨이를 연다.
이 검사는 파일 경로에 대한 검사이며 모델의 CUDA 실행 여부는 실제 PDF 호출로 검증한다.

## Docling 로컬 실행 환경

네트워크 볼륨의 작은 Python 파일을 요청마다 읽는 지연을 줄이기 위해 PDF worker와
Docling 자식 프로세스를 `/opt/a4b-envs/pdf/bin/python`으로 실행한다.
모델 원본은 기존 네트워크 볼륨 경로를 사용한다. 엔진 제한 시간 240초는 유지한다.

1. 새 배포본에서 `bash scripts/runpod/pdf-runtime.sh prepare`를 실행한다.
   `.pdf-versions/<버전>`이라는 별도 로컬 경로에 고정 패키지를 설치하고 CUDA를 검사한다.
   이 경로는 최종 경로이므로 가상환경의 절대 shebang을 나중에 바꾸지 않는다.
   기존 실행 환경과 실행 경로는 수정하지 않는다.
2. `$A4B_BASE_DIR/runtime-archives/pdf-versions/<버전>/`에 압축본과 체크섬·경로를 담은
   `manifest.json`을 모두 완성하고 검증한 뒤, `pdf-current` 심볼릭 링크 하나를 원자적으로
   교체한다. 설치·검증·압축·체크섬 기록이 실패하면 이전 선택과 이전 환경이 유지된다.
   새 선택이 게시된 직후 중단되더라도 다음 `restore`가 완성된 새 버전으로 진행할 수 있다.
3. `start-final.sh`는 서비스 실행 전에 `pdf-runtime.sh restore`를 호출한다.
   선택된 압축본을 검증해 원래 버전 경로로 복원한 뒤 `/opt/a4b-envs/pdf` 링크를 전환한다.
   기존 버전은 남겨 둔다. 기존 고정 디렉터리 또는 불완전한 환경은 `.backup-<고유값>`으로
   보존하고 복구하므로, 완료 표시가 없어도 정상 압축본으로 복구할 수 있다.
   보존 직후 중단되어 실행 경로가 사라져도 다음 복원이 같은 압축본으로 완료한다.
4. 이전 배포의 `pdf.tar.gz`와 `.sha256` 형식도 `pdf-current`가 아직 없을 때 복원할 수 있다.
   새 형식은 이 파일 쌍을 덮어쓰지 않는다. 압축본 손상은 검증 단계에서 중단하며,
   기존 정상 환경을 지우거나 임의의 다른 버전을 선택하지 않는다.

가상환경과 압축본은 같은 Python/CUDA 베이스 이미지에서 사용한다. 이미지 변경 시 다시 준비한다.
실행 경로를 바꾸는 `restore`는 서비스 시작 전 또는 서비스를 중지한 상태에서 실행한다.
`prepare` 성공만으로 실행 중인 서비스의 환경이 바뀌지는 않는다. 기존 시작 설정의
`A4B_PDF_VENV`, `A4B_PDF_ROUTER_PYTHON`, `EASYFLOW_DOCLING_PYTHON`은 같은 로컬 별칭을 사용한다.
이전 버전으로 복귀할 때는 보존된 버전의 압축본·manifest를 확인하고 `pdf-current`를
그 버전으로 원자적으로 교체한 다음 서비스를 중지한 상태에서 `restore`한다.
실패한 준비 디렉터리와 이전 버전은 자동 삭제하지 않는다. 운영에 사용되지 않는지 확인한 뒤 정리한다.

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
