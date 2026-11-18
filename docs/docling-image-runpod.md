# 이미지 표 보완의 RunPod Docling 연결

## 문제와 변경

이미지의 layout/PaddleOCR 요청은 RunPod를 사용하지만 표 영역은 AWS의 로컬 Docling으로 실행됐다. 2026-11-18 합성 영수증 요청에서 `image_table:dependency_missing`, `route=cpu_local`, `ModuleNotFoundError: No module named 'docling'`을 확인했다.

정규화된 PNG 표 영역 및 전체 이미지 비교를 `POST /v1/docling-image`로 보낸다. 202 작업 ID를 받고 `GET /v1/docling-image/{id}`로 결과를 조회하며 실패·취소 시 DELETE한다. 원본 PNG SHA-256, Docling 엔진·버전·CUDA 결과를 검사한다. 반환된 기존 blocks/cells 계약을 그대로 사용하므로 원본 영역·좌표 연결은 기존 파이프라인이 보존한다.

PDF와 이미지 Docling은 하나의 작업 슬롯과 취소·기한·polling lease를 공유한다. 입력은 8MiB 이하 PNG, 디코딩 크기는 4천만 픽셀 이하, 결과 JSON은 6MiB 이하이다. 이미지 실패를 로컬 Docling 재시도로 숨기지 않는다. 부분 추출 및 표 구조 누락은 계속 검토 대상으로 남는다.

Pod 내부 PDF worker는 `A4B_DOCLING_LOCAL_ONLY=1`로 이미 점유한 슬롯에 다시 요청하지 않는다. AWS에서는 이 값을 설정하지 않는다. Word/PPT/HWP 경로와 검산·CSV 저장 조건은 변경하지 않는다.

## 배포 순서

1. PR의 `Backend/documents`와 `scripts/runpod` 수정본을 포함한 새 RunPod 릴리스를 준비한다. 기존 Docling Python·모델을 재사용하며 패키지 변경은 없다.
2. 별도 localhost 포트와 독립 테스트 키로 이미지·PDF 계약을 검증한다. 운영 키를 복사하거나 로그에 출력하지 않는다.
3. 운영 파서의 진행 중 작업을 확인하고 새 릴리스로 파서 서비스를 전환·재시작한다. `/health`의 `docling_image_jobs=true` 및 인증된 이미지 요청을 확인한다. 시작 시 사용하는 릴리스 경로도 갱신한다.
4. 그 다음 AWS 코드를 배포한다. main 병합 시 기존 EC2 Actions 배포 대상은 backend이다. RunPod는 이 Actions로 자동 갱신되지 않는다. 기존 `A4B_PARSER_URL`, `A4B_PARSER_API_KEY`를 사용한다.
5. 같은 합성 영수증으로 홈페이지 표 추출을 재검증한다. 연결 성공과 숫자 정확도는 따로 판정한다.

되돌릴 때는 AWS 호출 코드를 먼저 이전 버전으로 돌린 뒤 RunPod를 복귀한다. AWS 새 코드가 이전 RunPod를 호출하면 404로 표 보완 실패가 기록된다. PR 검증용 별도 서버를 운영 반영 완료로 간주하지 않는다.

## 검증 기록 (2026-11-18)

- 실패 원본: 합성 테스트 영수증 SHA-256 `4fdfcd110b14f0d19081eff7b8d4f6a1dafef0791df6d2bc001b8548a669b8ef`. AWS 저장 결과와 해당 Docling `engine.log`를 대조했다.
- 로컬: 원격 이미지·PDF 클라이언트, HTTP 작업 큐, 파이프라인 회귀 테스트 68개 및 subtest 6개 통과. 새 원격 클라이언트 테스트는 GPU·Linux 전용 모듈 없이 수집된다.
- RunPod: A100-SXM4-80GB, 별도 localhost 검증 서버, 독립 인증 키, 기존 설치된 Docling/Paddle 모델. AWS 운영 컨테이너와 기존 파서 서비스는 변경하지 않았다.
- 합성 영수증 첫 검증: 22.14초, Docling 표 보완 18.07초. `cpu_local/failed/dependency_missing`에서 `gpu_remote/succeeded`로 변경. 경고 없는 전체 처리 결과와 13행×4열 표가 반환됐다.
- 정확도 한계: 표가 반환돼도 초콜릿 상품명·수량, 할인 값 등 일부 셀이 누락됐다. 이 변경은 연결 장애 해결이며 정확도 100%나 CSV 실행 완주를 주장하지 않는다. GS25 금액 오인식 역시 별도 문제다.
- 시간은 검증 클라이언트 파이프라인 기준으로 홈페이지 업로드·설계·CSV 생성 시간 또는 CPU/GPU 성능 개선율이 아니다.
- 최종 운영 엔진 경로 검증: 합성 영수증 20.96초(이미지 Docling 17.06초), GS25 2.08초, 합성 1쪽 PDF MinerU 69.23초, 같은 PDF Docling+OCR 20.10초. 모두 `succeeded`. PDF Docling CUDA peak 705,547,776 bytes 확인. 두 PDF 경로 모두 기존 API 계약 유지.
- 검증 후 임시 서버만 종료한다. 운영 적용은 RunPod 새 릴리스 우선 반영 후 AWS 배포 순서이며, 이 PR 검증만으로 운영 배포 완료를 의미하지 않는다.
