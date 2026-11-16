# 관리자 모니터링

관리자는 기존 `a4b.users.role='admin'`을 사용합니다. 로그인마다/요청마다 기존 인증 모듈이 회원 상태를 다시 읽으며, 이메일을 로그인 허용 목록으로 사용하지 않습니다. 일반 회원·비로그인·비활성 관리자는 `/api/admin/*`에 접근할 수 없습니다. 로컬 비인증 모드도 관리자 접근을 허용하지 않습니다.

## 화면과 API

관리자 로그인 후 사이드바 **모니터링** 또는 `/?screen=monitoring`에서 엽니다.

- 최근 24시간 / 7 / 30 / 90일, 생성·실행·문서 처리, 실패·차단·전체 결과, 처리 상태로 필터링합니다.
- 요청 ID·고유 회원 ID·노드·오류 코드로 검색하고 25건씩 조회합니다. 이메일·이름은 조회하지 않습니다.
- 요약과 오류 집계는 기간·유형 기준이며, 목록 검색·결과·처리 상태 필터와 구분합니다. 추가 질문 대기와 차단을 실패율에 합산하지 않습니다. 테스트 요청은 별도 구분 정보가 없어 포함됩니다.
- 상세 화면은 단계 기록(최대 100개), 오류 사유, 추적 ID·모델·배포 버전·검증 필드/유형을 표시합니다. 과거 데이터는 새 진단 필드가 없을 수 있습니다.
- 처리 상태/메모 변경은 revision 비교로 동시 수정 충돌을 감지하고 변경자를 기록합니다. 최신 30개 변경 이력을 표시하며 DB에는 전체 이력이 남습니다.

`GET /api/admin/monitoring?days=7&kind=all&status=failed&triage=all&page=1&q=`

`GET /api/admin/monitoring/{generation|execution|document}/{id}?days=7`

생성 요청의 API `id`는 `회원ID:요청ID` 형태입니다. 작업 공간이 다른 동일 요청 ID를 구분합니다.

`PATCH /api/admin/monitoring/{kind}/{id}`: `{status,note,revision}`. status는 `unreviewed`, `investigating`, `resolved`; note는 최대 2,000자입니다. 기존 same-origin 검사를 적용합니다.

## 데이터와 권한

`009_admin_monitoring.sql`을 DBA 마이그레이션 계정으로 적용합니다. SECURITY DEFINER 함수는 전용 NOLOGIN·NOBYPASSRLS 역할 `a4b_monitor_owner`가 소유합니다. RDS의 `postgres`도 BYPASSRLS가 없을 수 있으므로, 이 전용 역할에만 생성 이력 SELECT와 처리 이력 접근 정책을 부여합니다. 앱에는 이 역할의 멤버십을 부여하지 않습니다. 앱 연결에는 기존 `a4b_app`을 사용하며 DBA 연결을 앱 런타임에 전달하지 않습니다. 함수는 서버가 세션에서 가져온 회원 ID의 현재 관리자/활성 상태를 검사합니다. 이 함수의 actor 인자는 외부 HTTP 요청에서 받지 않습니다. 일반 작업 공간 RLS 정책은 변경하지 않습니다.

함수는 생성 요청, 실행/노드, 문서 버전의 진단 필드만 투영합니다. 요청 본문·문서 내용·모델 응답·입출력 JSON은 전달하지 않습니다. Node에서 필드를 다시 제한하고 알려진 비밀값, URL, 이메일 등을 마스킹합니다. 원문 예외 문자열을 새로 기록하지 않고 Pydantic 오류의 위치/유형과 HTTP 상태만 수집합니다. 운영 메모에도 마스킹을 적용하지만 민감한 원문은 입력하지 마세요.

신규 실행 실패와 생성 요청에는 `diagnostics`가 저장됩니다. `A4B_RELEASE`는 이미지 빌드의 커밋 값입니다. 로컬 미설정과 과거 기록은 미기록으로 표시합니다. 모델별 형식 오류의 상세가 제공되지 않는 예외는 예외 유형까지만 확인할 수 있습니다.

인증 실패·기록 시작 전 요청 실패 및 별도 Node 워크플로 엔진의 SQLite 실행은 이 첫 버전의 RDS 모니터링 범위에 포함되지 않습니다. 기존 실행·생성 이력의 자동 삭제나 서버 로그 수집기를 추가하지 않습니다.

## 최초 관리자 지정

운영 컨테이너에서 기존 TLS DB 환경으로 실행합니다. 가입과 이메일 인증이 완료되어 Cognito 고유 식별자로 연결된 활성 계정 하나만 허용합니다. 비밀번호 입력/변경은 하지 않습니다.

```sh
/app/Backend/.venv-agent/bin/python /app/scripts/deploy/set-monitor-admin.py --email skn33team5@gmail.com
/app/Backend/.venv-agent/bin/python /app/scripts/deploy/set-monitor-admin.py --email skn33team5@gmail.com --apply
```

배포는 기존 GitHub Actions를 사용합니다. `main` workflow에서 branch를 `JHD/admin-monitoring`, target을 `all`로 지정하면 기존 운영 서비스에 배포됩니다. 해당 브랜치를 main에 병합하지 않습니다. 자동 마이그레이션에는 기존 `/var/lib/a4b-deploy/migration-env`의 DBA 연결이 필요합니다. 마이그레이션 없이 새 앱을 먼저 시작하면 사전 검사가 실패합니다.

## 검증

`npm run check`, `npm run build`, `Backend/api/tests/admin-monitoring.test.ts`, 인증 회귀 테스트와 프론트 관리자 테스트, Python 생성 이력·진단 정보 테스트를 실행합니다. PostgreSQL 통합 테스트는 복원한 일회용 업무 스키마와 004/009 마이그레이션 위에서 `A4B_MONITOR_TEST_DATABASE_URL`로 실행합니다. DB 이름에 `test`가 있어야 하며 테스트 데이터는 롤백합니다.

## 2026-11-16 검증 및 반영 상태

- 운영 계정 `skn33team5@gmail.com`: 기존 활성 Cognito 회원의 고유 ID로 관리자 지정 완료. 비밀번호와 Cognito 계정은 변경하지 않았습니다.
- 타입 검사와 프론트/백엔드 빌드 통과.
- Node 모니터링·인증·프론트·저장소 관련 테스트: 41개 통과, 환경 조건부 3개 생략. 별도로 PostgreSQL 모니터링 통합 테스트 3개 전부 통과(일반 회원 거부, 관리자 권한 회수, 기존 RLS 유지, 동일 요청 ID의 작업 공간 분리, 메모 충돌/이력 포함).
- Python 진단 정보·생성 이력: 42개 통과. 전체 마이그레이션을 적용한 로컬 PostgreSQL·SQLite 저장소 회귀: 59개 전부 통과.
- Python workflow_engine 전체: 757개 통과, 1개 실패. `test_representative_and_extra_cases_cover_all_operation_ids`의 Slack 작업 예시 누락이며, 작업 전 HEAD `9bedf46` 소스 사본에서도 같은 실패를 재현했습니다. 본 변경으로 수정하지 않았습니다.
- 샘플 데이터로 실제 빌드 화면의 목록·상세 열기와 좁은 화면 배치를 확인했습니다. 운영 로그인 세션으로 화면을 검증한 것은 아닙니다.
- 운영 DBA 설정은 사용자의 숨김 입력으로 `/var/lib/a4b-deploy/migration-env`에 root 전용 권한 0600으로 저장됐고 연결을 확인했습니다. RDS postgres의 BYPASSRLS가 비활성임을 확인해 전용 함수 소유자 정책으로 보완했습니다. 운영 배포 결과는 별도 확인이 필요합니다.
