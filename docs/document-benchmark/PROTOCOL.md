# RunPod 문서 파이프라인 A/B 평가 조건

사용자 확정 범위: OmniDocBench 대표 PDF 18페이지, Pollock 실자료 CSV 100개,
형식별 보존 시험과 묶음 시험. 추론과 채점은 새 RunPod에서 실행한다.
로컬에서는 코드 작성과 전송 파일 준비만 한다.

## 비교 구성

- A: `8bd0709`의 자동 문서 분석·라우팅을 기본 설정으로 실행한다.
- B: 명시적인 `A4B_DOCUMENT_BENCHMARK_ARM=docling-paddle-fixed-v1`에서
  Docling과 PaddleOCR를 모두 호출한다. Docling 실패 시에도 OCR를 시도한다.
  PaddleOCR에는 원본에서 렌더링한 모든 페이지를 넘긴다.
- B 병합: Docling 본문·표를 먼저 유지한다. 공백을 제거한 OCR 문자열이
  해당 페이지의 Docling 문자열에 포함되면 중복 후보로 보존하고 본문에서는
  제외한다. 나머지 OCR 문자열은 해당 페이지 본문 뒤에 추가한다.
  모델 점수나 정답으로 결과를 선택하지 않는다.
- 기존 Docling 표준 설정의 EasyOCR(ko/en)도 명시적으로 기록한다.
  B의 PaddleOCR는 EasyOCR 실행 여부와 무관하게 별도 실행한다.
- PDF는 원본 페이지를 144 DPI로 렌더링한다. Office는 LibreOffice로
  OCR용 PDF를 만든다. CSV·TSV·JSON·TXT·MD의 OCR 입력은 원문 문자열을
  고정된 줄 길이·글꼴·페이지 크기로 출력한 PDF이다.
- CSV·JSON·XLSX의 서비스 행 로더는 A/B가 공유한다. 이 로더에서 계산한
  지표는 공통 로딩 검증이며, OCR 교체 효과로 해석하지 않는다.
- 사람의 채택·보류·검토 완료 기능은 주 평가에 사용하지 않는다.

## 자원과 제한

- 새 전용 Pod `a4b-document-ab-bmt-jhd-20261028`.
- 승인 사양: L40S 48GB 1대, RAM 62GB, 16 vCPU, 임시 디스크 120GB.
  표시 요금 약 $1.11/시간. 실제 GPU·드라이버·메모리는 실행 로그로 기록한다.
- A와 B는 같은 Pod에서 파일 동시 처리 수 1로 실행한다. 각 실행은 새 서비스
  데이터 디렉터리·SQLite를 사용한다. 양쪽 모두 파일당 분석 프로세스 상한
  300초, HTTP 클라이언트 대기 상한 330초, 파일 8MiB와 서비스의 행·열·객체
  제한을 그대로 적용한다.
- MinerU의 Torch 소형 모델 CUDA 확인과 VLM의 GPU 확인을 분리한다.
  Linux llama.cpp는 Vulkan을 사용하므로 NVIDIA EGL ICD를 명시하고
  `MINERU_LLAMA_CPP_BACKEND=vulkan`으로 CPU 폴백을 허용하지 않는다.
  같은 GGUF 가중치를 실제 로드·생성한 GPU 증거가 있어야 본 측정을 시작한다.
- 모델은 프로세스 단위로 로드하는 현재 서비스 동작을 포함한다. 다운로드와
  설치 시간은 품질·업로드 소요 시간에서 제외하고 별도로 기록한다.
- 한 파일 실패가 뒤의 파일 처리를 중단시키지 않도록 한다. 모든 입력과 실패를
  결과 목록에 남긴다. 파서 일부 실패·HTTP 성공·전체 완료를 구분한다.
- B는 A와 같은 엔진별 240초 상한을 사용한다. 파일 내부의 290초 예산을
  공유하며 첫 Docling 실행에는 OCR 시도를 위한 30초를 남겨 둔다.
  Docling이 일찍 끝나면 OCR는 남은 예산을 최대 240초까지 사용한다.
  양쪽 분석 프로세스 상한은 300초이고 저장·응답 대기 여유를 포함한
  HTTP 클라이언트 대기 상한은 330초이다.

## 데이터와 정답 고정

- OmniDocBench 코드 `v1_0`, commit
  `337cc26965893db3ef53ddc119a6d6bb5bde096f`.
- HF 데이터 `v1_0`, commit `f5f559bddf50e36f7f9899d842d0006f13ce8afc`.
  해당 JSON의 페이지 모집단과 실제 제공 PDF 수를 기록한다.
- `data_source` 9종마다 2페이지를 고른다. 파싱 전 고정 seed `20261028`로
  추출하고, 가능하면 서로 다른 원본 문서에서 뽑는다. 입력 PDF와 정답 JSON의
  SHA-256을 추론 전에 manifest에 저장한다.
- Pollock commit `bd2a646f4cf55d2bd4e76df511c9c9f675ae7bcc`의
  `survey_sample/csv` 100개와 대응하는 `clean` 100개를 모두 사용한다.
  큰 파일·헤더 오류·인코딩 실패도 제외하지 않고 상태와 분모에 남긴다.
- 추가 보존 시험은 논문 데이터와 분리한다. 파일 생성 전에 고정한 레코드에
  선행 0, 숫자, 빈값, 한글, 인용부호, 줄바꿈을 넣고 형식별 기대값을 저장한다.
  CSV에는 문자열을 기대하며 JSON/XLSX에는 자료형까지 비교한다.
- StatSheets는 셀 역할 분류와 표 범위 탐지 기능이 마련된 뒤 평가한다.

## 실행과 채점

- 실제 HTTP 업로드와 DB 저장 결과를 사용한다. 첫 3행 preview가 아니라
  SQLite의 전체 `rows_json`을 채점한다. 원본 다운로드 SHA-256도 대조한다.
- 단일 입력 후 같은 형식 5개·10개, PDF/XLSX/CSV/JSON/TXT/MD 혼합,
  같은 이름·다른 내용, 정상·손상 혼합, 혼합 입력의 역순을 시험한다.
- 파일 instance ID와 corpus ID를 따로 둔다. 파일명·내용 해시만으로 식별하지
  않는다. 출처 오연결·누락·중복·손상 파일 격리 여부를 기록한다.
- PDF: 고정된 OmniDocBench 공식 매칭·정규화·Edit Distance·TEDS·읽기 순서
  코드를 사용한다. 실패 파일도 빈 예측 파일로 만들어 채점 모집단에 남긴다.
  CDM 수식 점수는 이번 범위에 포함하지 않는다.
- CSV: 고정된 Pollock `header_record_cell_measures_csv` 함수를 그대로
  사용한다. 공식 정의의 정규화와 Precision/Recall 분모도 바꾸지 않는다.
- 추가 보존: 원래의 고정 정답과 기대값·구조·자료형을 비교한다.
- 결과는 파일별 품질과 묶음 처리 품질로 분리한다. 성공 파일만의 평균과
  실패를 포함한 전체 평균을 명시하며, 다른 지표를 합친 종합점수는 만들지 않는다.
- 결과와 로그를 로컬로 회수한 뒤 새 Pod를 중지한다. 임시 디스크 파일은
  중지 시 삭제될 수 있으므로 회수와 해시 대조를 먼저 마친다.

## 근거

- [OmniDocBench 공식 저장소](https://github.com/opendatalab/OmniDocBench)
- [OmniDocBench 데이터](https://huggingface.co/datasets/opendatalab/OmniDocBench)
- [Pollock 공식 저장소](https://github.com/HPI-Information-Systems/Pollock)
- [MinerU 4 설치](https://opendatalab.github.io/MinerU/quick_start/)
- [Docling GPU 설정](https://github.com/docling-project/docling/blob/main/docs/usage/gpu.md)
