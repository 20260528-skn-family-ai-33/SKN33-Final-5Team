# 리뷰 오류 수정과 검증 — 2026-10-13

대상은 A4B integrated v2이며 다운로드 원본과 이전 데모는 유지했다. 수정 코드는 별도 A4B-integrated-v2 작업 폴더에 있다.

## 수정

1. save_agent는 Schedules의 스키마 생성 후 BEGIN IMMEDIATE로 설정·예약·소스를 저장하고 최상위에서만 commit/rollback한다. 하위 configure/replace_source는 commit=False로 참여한다. 멱등 저장은 기존 부분 저장의 누락 소스를 복구하지만 정상 예약은 중지하지 않는다.
2. replace_source 검증 문맥은 새 표와 기존 문서 자원을 함께 유지한다. 명시적인 read_document 정책 ID를 제거하지 않는다.
3. builder_run_leases에 실행 토큰·만료·Python PID·활성 상태를 저장한다. Node worker는 실행 ID를 미리 정하고 timeout/비정상 반환 시 해당 소유자의 실행만 실패로 확정한다. 시작/5초 예약 검사에서 죽은 PID·만료·legacy running을 복구한다. 다른 작업자가 소유권을 갱신했다면 이전 토큰으로 중단시킬 수 없다.
4. 20초 heartbeat로 실행(90초)과 예약(5분) lease를 갱신한다. claim_due는 즉시 실행할 작업 한 개만 가져온다. 노드 전후 소유권 검사, 보고서 원자 교체 중 DB 소유권 잠금, 최종 DB 쓰기 소유권 검사로 오래된 소유자의 결과 반영을 막는다. 정상 완료 직후 heartbeat 응답은 finished로 처리해 잘못된 실패 안내를 방지한다.
5. 재시도는 같은 실행·설정·체크포인트를 사용하고 attempts에 실패와 성공을 남긴다. 예약 attempt_history와 최종 상태도 동기화하며 UI에서 재시도 이력을 표시한다.

## 추가 회귀 검사

`python/agent_builder/test_review_fixes.py`의 15개 검사:

- 정책+표 예약 생성, 소스 교체, 정책 원문을 포함한 검수 실행
- 초기화 예외 시 기존 버전/예약 유지 및 신규 설정 무잔여 롤백
- 기존 부분 저장의 멱등 복구, 정상 멱등 저장 시 활성 예약 유지
- 살아 있는 worker 보호, 죽은 PID 즉시 복구, 만료 소유권 복구
- 이전 토큰의 heartbeat·중단·완료·보고서 쓰기 거부
- heartbeat 중 301초 시점 재점유 거부, heartbeat 중단 후 재점유
- 한 작업씩 claim
- 예약 실패→같은 run 재시도 성공, 양쪽 최종 상태와 실패 이력 보존
- 실제 자식 프로세스 강제 종료→복구→체크포인트 재시도
- 정상 완료 후 heartbeat를 실패로 처리하지 않음

`server/worker-lifecycle.test.ts`: 실제 worker timeout을 짧게 설정해 Python 자식을 강제 종료하고, API 응답 전에 failed 기록이 저장됐는지 검사한다. 같은 실행 ID를 재시도해 성공과 실패 이력 보존을 확인한다. 모델은 대역이며 외부 API를 호출하지 않는다.

## 결과

Python 103개 통과(기존 88 + 추가 15). 서버 510개 중 509개 통과, 기존 1개 제외, 실패 0. TypeScript·프런트/서버 빌드 통과. 보고서·승인 HTTP와 worker timeout은 최종 Python 변경 후 별도 재검증했다.

이 검증은 로컬 코드·상태·복구 경로 검사다. 실제 다중 서버 장시간 부하·모델 정확도·Windows 실행 검증은 포함하지 않는다. 부모가 종료됐지만 Python 자식이 남아 있으면 즉시 사망 감지는 불가능하므로 heartbeat 만료 후 복구된다. 실패한 검수 노드 내부에서 이미 수행된 일부 행의 모델 호출은 재시도 시 반복될 수 있다.

## 기존 데이터·실제 데모 확인

이전 데모의 SQLite는 backup API로 독립 복사하고 원본은 보존했다. 기존 자동화 9개를 유지한 새 서버에서 팀별 합계 A=0.3/B=200, 서버 CSV 다운로드, 시험 예약 등록·취소, 정규 예약 중지를 실제 HTTP로 확인했다. 화면 결과는 review-fixed-demo-2026-10-13.png, API 기록은 REVIEW_FIXES_LIVE_CHECK.json에 있다. 이 확인에서는 외부 모델 API를 호출하지 않았다.
