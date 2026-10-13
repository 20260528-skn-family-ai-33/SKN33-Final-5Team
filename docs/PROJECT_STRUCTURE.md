# Backend·Frontend 폴더 정리

2026-10-13에 Python 코드와 화면·Node 서버를 분리했습니다. 저장소 이름은 `SKN33-Final-5Team`이며 기존 로컬 checkout 폴더 `finalproject`는 유지합니다.

| 이전 위치 | 현재 위치 | 역할 |
|---|---|---|
| `python/agent_builder/` | `Backend/agent_builder/` | 에이전트 생성·실행·예약·검수·승인 및 Python 테스트 |
| `python/*document_worker.py` | `Backend/scripts/` | 문서 변환 worker |
| `integrations/workflow_preprocessing/`의 코드·명세 | `Backend/integrations/workflow_preprocessing/` | 전처리 생성기·계약·도구 명세 |
| 전처리 JSONL·합성 업무 레코드 | `docs/examples/workflow_preprocessing/` | 합성 입력·정제 결과·학습/검증/시험 예제 |
| `scripts/*.py`·Python 설치 스크립트 | `Backend/scripts/` | 데이터 가져오기·배포용 패키징·설치 |
| `app/`, `components/`, `hooks/`, `lib/` | `Frontend/` 아래 동일 이름 | 화면·공통 타입·화면 데이터 처리 |
| `server/` | `Frontend/server/` | HTTP API·Node 실행 엔진·Python 연결·서버 테스트 |
| `public/`, `vendor/` | `Frontend/` 아래 동일 이름 | 웹 정적 리소스·외부 스타일과 라이선스 |
| `scripts/*.ts`·시작 스크립트 | `Frontend/scripts/` | 실행·회귀 검사·실호출 검증 |
| `package*.json`·Vite·TypeScript·PostCSS 설정·HTML | `Frontend/` | 화면·Node 패키지와 빌드 설정 |
| `requirements-agent*.txt` | `Backend/` | Python 의존성 |
| `examples/` | `docs/examples/` | 합성 시연·회귀 검사 자료 |
| `docs/*.png` | `images/` | 문서용 화면 이미지 |
| `START_HERE.md` | `docs/START_HERE.md` | 빠른 시작 |

## 설치와 실행

모든 명령은 저장소 최상위 폴더에서 실행합니다.

```bash
./Backend/scripts/setup-agent-demo.sh
npm --prefix Frontend ci
npm --prefix Frontend run dev
```

이후 실행은 `./Frontend/scripts/start-agent-demo.sh`입니다. `Frontend/` 안에서는 `npm run dev`, `npm run check`, `npm test`를 사용합니다. macOS 더블클릭 시작 파일은 `Frontend/scripts/실행.command`입니다.

새 Python 환경은 `Backend/.venv-agent/`에 설치합니다. 기존 최상위 `.venv-agent/`는 내부에 설치 당시의 경로가 기록되어 있어 이동하지 않았으며, 새 환경이 없으면 기존 환경을 사용합니다. PDF 변환 환경 설치는 `./Backend/scripts/setup-document-demo.sh`입니다.

`Frontend/.env.example`을 `Frontend/.env`로 복사하면 선택 설정을 저장할 수 있습니다. `npm run dev`와 `npm start`가 이를 읽습니다. 다른 Python 환경은 `A4B_AGENT_PYTHON`, `EASYFLOW_DOCLING_PYTHON`에 실행 파일의 절대 경로를 지정합니다.

## 운영 데이터와 전처리

저장소 최상위 `data/`의 DB·보호 키·원본 업로드·보고서는 보존했습니다. Node 서버와 Python 가져오기 도구는 동일한 최상위 데이터 폴더를 기본값으로 사용합니다. `EASYFLOW_DATA_DIR`의 절대 경로는 그대로 사용하고 상대 경로는 저장소 최상위를 기준으로 해석합니다.

`Backend/integrations/`에는 전처리 코드와 명세만 둡니다. 합성 데이터는 `docs/examples/workflow_preprocessing/`에 있으며, 생성기의 출력도 코드 폴더와 분리합니다. 전처리 가져오기는 다음 명령입니다.

```bash
Backend/.venv-agent/bin/python Backend/scripts/import-preprocessing.py
```

기존 최상위 환경을 사용하는 경우 명령 앞의 `Backend/.venv-agent/bin/python`을 `.venv-agent/bin/python`으로 바꿉니다. 이 가져오기는 기존과 동일하게 합성 데이터를 별도 테이블에 저장하며 사용자 워크플로우를 생성하지 않습니다.

## 확인과 컨테이너 실행

```bash
npm --prefix Frontend run check
npm --prefix Frontend run test:python
npm --prefix Frontend test
docker build -t a4b .
docker run --rm -p 3030:3030 -v a4b-data:/app/data a4b
```

Dockerfile은 저장소 최상위에서 Backend·Frontend를 함께 구성합니다. Node 빌드 결과는 `Frontend/dist-app/`와 `Frontend/build-server/`에 생성합니다. 실제 Docker 실행 검증 여부는 README의 검증 기록을 확인하세요.

## 복사본과 복구 자료

원본과 바이트 단위로 동일한 이름 끝 ` 2` 복사본 121개, 이전 ZIP 전달용 manifest·안내, 이전 빌드 결과는 checkout 밖의 `../docs/backups/folder-restructure-20261013-123110/`에 보관했습니다. 같은 위치의 `source-before/`에는 정리 전 Git 추적 파일, `identical-copies.json`에는 복사본 경로와 SHA256을 저장했습니다. 원본 운영 데이터는 백업 폴더에 복사하거나 이동하지 않았습니다.

폴더 정리는 로컬 파일 변경만 수행했습니다. Git commit·push는 수행하지 않았습니다.

## 참고

[SKN14-FINAL-3Team](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN14-FINAL-3Team)은 실행 코드·설계 문서·문서용 이미지를 최상위에서 구분하는 구조를 참고했습니다. 이 프로젝트는 Python 에이전트를 `Backend`, React·Node 애플리케이션을 `Frontend`로 구분합니다.
