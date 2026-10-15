# macOS·Windows 두 환경 지원 — 2026-10-13

팀원 5명(macOS 3명, Windows 2명)이 같은 명령으로 설치·실행·검사하도록 맞춘 기록입니다. Windows 11, Node.js 24.19.0, Python 3.13.3에서 직접 확인했습니다. macOS 동작은 바꾸지 않는 것을 원칙으로 했고, Windows에만 필요한 처리는 `process.platform==='win32'` 조건으로 분리했습니다. macOS 확인은 GitHub Actions(`.github/workflows/ci.yml`)의 `macos-latest` 실행으로 합니다.

## 공통 명령

저장소 최상위 폴더에서 두 OS 모두 같은 명령을 사용합니다.

```bash
npm --prefix Frontend ci
npm --prefix Frontend run setup
npm --prefix Frontend run dev
```

| 명령 | 하는 일 |
|---|---|
| `run setup` | Python 3.12 → 3.13 → 3.11 → 3.14 순서로 찾아 `Backend/.venv-agent`를 만들고 잠금 파일을 설치 (`Frontend/scripts/setup-python.mjs`) |
| `run setup:docling` | 선택 사항. `Backend/.venv-docling`에 Docling 설치 |
| `run demo` | 이미 실행 중이면 브라우저만 열고, 아니면 빠진 설치 → 빌드 → 서버 시작 (`Frontend/scripts/launch-demo.mjs`). 실행 여부는 즉시 응답하는 `/api/health`로 확인 |
| 더블클릭 | macOS `Frontend/scripts/실행.command`(기존), Windows `Frontend/scripts/실행.cmd`(신규) |

기존 macOS 스크립트(`setup-agent-demo.sh`, `start-agent-demo.sh`)는 그대로 동작합니다.

## Windows에서 고친 앱 문제

| # | 증상 | 원인 | 수정 |
|---|---|---|---|
| 1 | 화면이 빈 흰색 | 정적 파일 경로 검사가 `/`만 처리해 JS·CSS 요청에 index.html을 돌려줌 | `path.sep` 사용 (`server/index.ts`) |
| 2 | 화면에서 보낸 한글이 Python에서 깨짐 | Windows 파이프 기본 인코딩이 cp949 | worker의 stdin·stdout을 UTF-8로 지정 (`worker.py`). 수정 후 DB에 한글이 정상 저장되는 것을 확인 |
| 3 | 실행 복구가 오류로 멈춤 | `os.kill(pid, 0)`이 Windows에서는 생존 확인이 아니라 Ctrl+C 신호 | Windows는 `OpenProcess`·`GetExitCodeProcess`로 확인 (`run_lifecycle.py`) |
| 4 | 예제 JSONL 읽기 오류, 문서 변환 결과 인코딩 불일치 | 파일 인코딩을 지정하지 않아 cp949 사용 | `encoding='utf-8'` 명시 (`preprocessing.py`, 문서 worker, 패키징 도구) |
| 5 | PDF를 여러 번 읽으면 서버 전체가 꺼짐 | pdf.js·exceljs를 불러온 worker thread가 끝날 때 Node가 간헐적으로 비정상 종료. 원래 코드에서도 PDF 6회 반복 시험 8번 중 5번 재현 | Windows에서만 문서 파싱을 별도 Node 프로세스로 실행. 크래시가 나도 그 프로세스만 끝남. 같은 시험 10번 중 0번 (`document-files.ts`) |
| 6 | 실행이 대기 중일 때 서버를 끄면 비정상 종료 | Windows에서 `process.exit()`가 남은 libuv 작업과 겹치면 `!(handle->flags & UV_HANDLE_CLOSING)` assertion | Windows에서만 IPC를 닫고 남은 작업을 마친 뒤 종료(2초 뒤 강제). 종료 전 worker 종료 대기 (`index.ts`, `worker-exit.ts`). 5번 중 5번 정상 종료 |

5의 PDF 응답 순서도 바로잡았습니다. pdf.js 정리(`destroy()`)를 마친 뒤 결과를 보냅니다.

## 테스트 쪽 수정 (macOS 기준값 유지)

| 실패 원인 | 수정 |
|---|---|
| Windows에는 SIGTERM이 없어 서버가 즉시 강제 종료됨 | 테스트 서버를 IPC 채널과 함께 띄우고, Windows에서는 `'shutdown'` 메시지로 같은 정상 종료를 요청 (`server/test-process.ts`) |
| tsx로 서버를 띄우는 데 Windows에서 20~25초 걸림(macOS는 수 초). `@langchain/langgraph`를 tsx로 불러오는 시간이 대부분 | 서버 시작·종료 대기와 해당 테스트 제한 시간을 Windows에서만 10배 (`scaled()`) |
| 열린 SQLite·폴더를 지울 때 EBUSY | 저장소를 닫은 뒤 삭제, 삭제 재시도 (`removeDirectory()`) |
| POSIX 권한(0600) 검사 | Windows에서는 생략. Windows는 사용자 프로필 ACL이 키 파일을 보호 |
| shebang 가짜 Python 실행 | Windows는 `sitecustomize.py`로 같은 지연을 주입 |
| `loop-api` 실패 시 테스트용 HTTP 서버가 남아 테스트가 끝나지 않음 | 서버 종료가 실패해도 정리하도록 순서 수정 |
| 테스트가 멈추면 끝나지 않음 | `npm test`에 `--test-timeout=900000` |

## 설치·저장소 설정

- `Backend/requirements-agent-lock.txt`: `tzdata`를 Windows에서만 설치 (`sys_platform == "win32"`)
- `.gitattributes`: 모든 텍스트 파일 LF, `.cmd`·`.bat`·`.ps1`만 CRLF. Windows에서 커밋해도 셸 스크립트가 CRLF로 바뀌지 않음. 저장소에 이미 있는 파일 내용은 바뀌지 않음
- `.github/workflows/ci.yml`: main push와 PR마다 macOS·Windows에서 설치 → 타입 검사 → Python 테스트 → 빌드·서버 테스트

## Windows 검증 결과

| 검사 | 결과 |
|---|---|
| `npm run test:python` | 172개 통과 (수정 전에는 수집 단계에서 cp949 오류로 중단) |
| `npm run check` | 통과 |
| `npm test` | 522개 중 521개 통과, 1개 제외(Docling 미설치), 실패 0. 약 5분. 수정 전에는 10개 실패와 `loop-api` 무한 대기 |
| 새 PC 기준 `npm run setup` | 빈 폴더에서 Python 3.13 선택, 163초에 설치, deepagents·langgraph·tzdata 불러오기 확인, 다시 실행하면 건너뜀 |
| `npm run demo` | 빈 데이터 폴더에서 12초 만에 시작(화면·health·노드 목록 응답 확인). 이미 실행 중이면 0.5초 안에 브라우저만 열고 종료 |
| 화면 | 노드 만들기·워크플로우 설계·서비스 실행 화면 확인, 한글 노드 저장 후 DB 확인 |

## 확인하지 못한 것

- macOS 실행: 이 PC에서는 할 수 없어 GitHub Actions 결과로 확인해야 합니다. macOS 경로는 코드상 바뀌지 않았거나(문서 파싱, 종료 처리) 플랫폼 조건으로 분리했습니다. 단, 공통 변경(테스트 IPC 채널, `npm run setup`, `.gitattributes`)은 macOS에서도 실행됩니다.
- Windows 10, 다른 백신 환경.
- tsx가 Windows에서 느린 근본 원인(파일 확인 비용)은 그대로입니다. Windows에서 `npm run dev`는 서버가 켜지기까지 20~30초 걸립니다. 빌드본으로 실행하는 `npm run demo`(또는 `npm start`)는 약 1.5초입니다.
- Docling을 설치하지 않아 Docling 실제 변환 검사 1개는 이번에도 제외됐습니다.
