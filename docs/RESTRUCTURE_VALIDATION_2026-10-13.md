# 폴더 정리 검증 — 2026-10-13

요청한 Backend·Frontend·docs·images 구조로 이동하고 실제 실행에 사용되는 경로를 수정했습니다. 정리 대상은 기존 `finalproject/` checkout이며 로컬 폴더 이름·Git 연결은 유지했습니다.

## 변경

- Python 에이전트·문서 worker·설치·가져오기·패키징 도구를 `Backend/`로 이동했습니다.
- React 화면·Node 서버·설정·검증 도구·설치된 Node 패키지를 `Frontend/`로 이동했습니다.
- 전처리 코드·계약·도구 명세는 `Backend/integrations/`, JSONL과 합성 레코드는 `docs/examples/workflow_preprocessing/`로 분리했습니다.
- 시연 파일은 `docs/examples/`, 문서 이미지는 `images/`로 이동했습니다.
- 공통 경로 함수는 소스와 빌드 모두 저장소 기준 Backend·자료·문서·DB를 찾습니다. Windows의 `Scripts/python.exe`와 기존 최상위 가상환경도 지원합니다.
- `.env.example`, 시작·설치 명령, Dockerfile과 제외 규칙, 문서 링크를 반영했습니다.

## 검사 결과

Node.js 24.19.0, Python 3.14.4, macOS에서 확인했습니다.

| 검사 | 결과 |
|---|---|
| `npm --prefix Frontend run test:python` | 172개 통과 |
| `npm --prefix Frontend run check` | 통과 |
| `npm --prefix Frontend test` | 빌드 성공, 522개 중 521개 통과·1개 제외·실패 0 |
| 소스·빌드 서버를 별도 임시 폴더에서 실행 | 화면 HTTP 200, 실제 Python 노드 목록 API HTTP 200, 임시 DB 생성 |
| 기본 데이터·상대 환경 경로·예제 조회 | 저장소 최상위·Frontend·임시 폴더에서 동일 결과 |
| 전처리 재생성 | JSONL 5개와 mock_sources.json 모두 원본과 바이트 동일 |
| 전처리 코드·명세 보호 | 재생성 전후 원본 유지, integrations에 출력하는 요청 거부 |
| 전처리 가져오기 도구 | 다른 실행 폴더에서 절대 경로·상대 환경 설정·상대 CLI 설정 모두 임시 DB에 30개 사례 저장 |
| 정리 전 Git 추적 파일 대응 | 337개 모두 현재 경로에 존재; 291개 동일, 46개 경로·안내 수정 |
| 이름 끝 ` 2` 복사본 | 121개 모두 원본과 동일, checkout 밖 백업으로 이동 |
| 문서 상대 링크·Git 제외 규칙·셸 구문·공백 검사 | 통과 |

제외된 1개는 기존의 Docling 실제 문서 변환 선택 검사입니다. 해당 실행 환경을 별도로 지정하지 않아 실행하지 않았습니다. 회귀 검사는 모델 대역과 임시 저장소를 사용하며 유료 모델 호출은 수행하지 않았습니다.

최초 서버 테스트에서는 격리 환경이 로컬 서버의 포트 열기를 차단했습니다. 로컬 서버 사용 권한으로 같은 전체 검사를 다시 실행한 결과 위와 같이 통과했습니다.

Docker CLI는 있지만 Docker 서비스 소켓이 없어 컨테이너 빌드·실행은 확인하지 못했습니다. Dockerfile의 Backend·Frontend·예제 복사 경로와 `.dockerignore`는 새 구조에 맞게 검토했습니다. Windows 실제 실행은 검증하지 않았습니다.

## 보존과 복구

최상위 `data/`의 DB·보호 키·원본 업로드·보고서는 이동하거나 시험에 사용하지 않았습니다. 기존 최상위 `.venv-agent/`는 설치 경로를 보존하며 새 Backend 가상환경이 없을 때 사용합니다.

정리 전 소스와 동일 복사본·이전 배포 manifest·이전 빌드 결과의 백업 위치는 `../docs/backups/folder-restructure-20261013-123110/`입니다. Git commit·push는 수행하지 않았습니다.

전체 경로 대응과 시작 명령은 [폴더 정리 안내](PROJECT_STRUCTURE.md), 프로젝트 설명은 [README](../README.md)를 참고하세요.
