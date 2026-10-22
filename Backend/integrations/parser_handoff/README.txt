팀 공유용 파서 스냅샷 — 2026-10-21, LightOn 후보 선정 개선 전

1. 구조
Docling + EasyOCR(ko/en) 기본 추출 → 선택적 LightOn 그림 텍스트 보완
→ 표 경계/본문형 표/다단 헤더 후처리 → PyMuPDF4LLM 내장 텍스트 누락 보완.
본문·페이지·섹션·표 Markdown/HTML·보완 보고서·코드 해시를 출력한다.
scripts에는 두 워커, 공통 모듈, 모델 준비 도구와 회귀검사가 있다.
db-contract-v0.1은 별도 연동 계약 예시이며 워커 원출력과 동일한 스키마로 간주하지 않는다.

2. 실행
Python 환경에 docling, docling-core, torch, easyocr, transformers, pymupdf,
pymupdf4llm, Pillow, beautifulsoup4, markdown-it-py를 준비한다.
정확한 검증 버전은 evidence의 실험 기록을 참조한다. 가중치/가상환경은 포함하지 않았다.
python scripts/builder_document_worker.py INPUT.pdf OUTPUT.json
python -m unittest discover -s scripts -p "test_*.py"
설정: A4B_PDF_PARSER_MODE=standard (기본), A4B_PDF_DEVICE=auto (기본).
PDF_LIGHTON_ENABLED=1인 경우에만 LightOn을 사용한다. 기본은 0.
LightOn은 standard + CUDA 필수. HF_HOME, A4B_PDF_OCR_MODEL_DIR,
A4B_PDF_ARTIFACTS_DIR는 준비된 캐시를 가리키도록 설정한다.
현재 코드의 EasyOCR download_enabled=False, LightOn local_files_only=True.
prepare_parser_models.py는 별도 모델 준비 단계에 사용한다.

3. LightOn 제한
그림의 OCR 후보를 작은 영역으로 읽고 두 맥락이 일치할 때만 반영한다.
기존 그림 텍스트가 3개 미만이어도 적격 OCR 2개(각 신뢰도 >=0.5)가 있으면 검사한다.
숫자 포함 후보는 제외한다. 원본 내장 텍스트와 표를 LightOn으로 덮어쓰지 않는다.
이미지 OCR의 순수 숫자 오인식은 독립 OCR >=0.95와 두 VLM 결과가 일치할 때만 예외 교체한다.
모델 일치는 정확성 보장이 아니며, 거절·오류·미완료 내역은 lighton_report에 남긴다.
PyMuPDF4LLM 보완은 페이지 끝에 추가되므로 읽기 순서와 표 셀 관계를 복원하지 않는다.

4. 기존 평가 해석
service-benchmark: 개발 10페이지, 우리86.11s/문구60/60, GLM68.67s/31/60,
LightOn 전체209.21s/52/60. 초기화 별도, 당시 버전의 단일 실행 결과.
selection2-v2: 기존10페이지 출력 유지, 후보 페이지의 일부 누락만 복구.
table-recovery, multilevel-header: 삼성 보고서 개발 자료의 표 관계 개선.
bis-holdout: 새 영문 디지털 문서10페이지42.56s, 문구40/40, 관계32/32,
2표 TEDS .968085. 이 샘플에서는 보완 모델/표 복구가 작동하지 않았다.
문구 검출률은 전체 문자 정확도가 아니다. 소수 표의 TEDS도 전체 품질을 보장하지 않는다.
HTTP/DB/업로드/대기열 포함 E2E 측정이 아니다. 스캔 문서 일반화는 미검증.
이번 후보 선정 개선의 결과는 아직 이 스냅샷에 포함하지 않았다.

5. 공유 범위
코드·계약 예시·요약 근거만 포함한다. 자격증명, .env, SSH 키, 모델 가중치,
가상환경, 원격 서버 접속 정보는 포함하지 않는다. SHA256SUMS.json으로 무결성을 확인한다.
