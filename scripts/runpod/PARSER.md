# 통합 파서 소스와 배포

`Backend/documents/analysis`, `Backend/documents/parsers`와 아래 RunPod 모듈은
같은 Git 커밋에서 배포한다. 운영본의 파일을 개별 수정해 별도 버전으로 관리하지 않는다.

- `gpu_parser_server.py`: 이미지 layout/OCR와 비동기 PDF HTTP 경로
- `pdf_jobs.py`: PDF 작업 접수, 직렬 실행, 결과 보관
- `pod_pdf_worker.py`: MinerU/Docling 라우터 실행과 결과 ZIP 생성
- `setup-pdf-router.sh`: 기존 CUDA 환경을 유지하면서 Docling 환경 준비

앱의 `analyze_document.py`는 `A4B_PARSER_URL`이 있으면 PDF 원본을 서버로 보낸다.
서버의 PDF worker는 같은 소스의 `pipeline.run(..., pdf_primary=...)`를 직접 호출한다.
PDF 내부의 이미지 보완은 같은 팟의 layout/PaddleOCR 서버로 요청한다.

## 환경별 설정

| 변수 | 용도 |
| --- | --- |
| `A4B_PARSER_URL`, `A4B_PARSER_API_KEY` | 앱이 사용하는 파서 주소와 키 |
| `A4B_PARSER_PORT`, `A4B_PARSER_BIND` | 서버 포트(기본 8100), 바인드 주소(기본 0.0.0.0) |
| `A4B_PDF_ROUTER_PYTHON` | PDF worker용 Python. 없으면 PDF 접수 503 |
| `A4B_MINERU_PYTHON` | 설정되어 있으면 Pod의 기본 PDF 엔진은 MinerU, 없으면 Docling |
| `A4B_PDF_ARTIFACTS_DIR`, `A4B_PDF_OCR_MODEL_DIR` | 준비된 Docling/OCR 모델 경로 |
| `A4B_POD_PADDLE_URL` | PDF 보완용 이미지 엔진 주소. 기본은 로컬 서버 포트 |
| `A4B_API_AUTH_REQUIRED` | 기본 인증 사용. 명시적으로 0이면 기존 Pod의 인증 우회 정책 유지 |

소스 통합 자체는 운영 인증 정책, 모델, 패키지 버전, 저장 경로를 변경하지 않는다.
키 값은 Git이나 배포 manifest에 포함하지 않는다.

## 배포 확인 순서

1. `/workspace/a4b/backups/parser/<작업시각>/`에 현재 소스와 시작 설정을 백업하고 해시를 검증한다.
   실행 환경 백업에 키가 포함된다면 접근 권한을 600으로 제한하고 서버 안에만 보관한다.
2. 로컬 문서 테스트와 `Backend/tests/deployment/test_runpod_parser.py`를 실행한다.
3. 확정한 Git 커밋의 소스를 `/workspace/a4b/parser-releases/<커밋>/`에 배치한다.
   manifest에 커밋과 각 소스의 SHA-256을 기록해 로컬·압축본·배포본을 대조한다.
4. 기존 환경과 모델을 사용해 임시 루프백 포트에서 OCR·PDF 실제 처리를 확인한다.
5. 진행 중인 PDF 작업이 없는지 확인한 다음 기존 시작 경로를 새 배포본으로 전환한다.
   현재 supervisor는 파서 종료 시 Ollama와 게이트웨이도 다시 시작하므로,
   운영 전환은 해당 서비스 영향까지 확인한 뒤 수행한다.
6. 기존 8100 주소로 앱 연결, PDF·OCR, 소스 해시를 확인한다. 실패 시 이전 경로로 복원한다.

이번 통합은 기존 동작 보존 범위다. Docling 비교 정책의 페이지 간 본문 중복,
PDF 취소·작업 만료 정책, 모델 상주화 등은 별도 변경하지 않는다.
