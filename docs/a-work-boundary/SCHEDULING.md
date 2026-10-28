# 주간 예약 트리거 검토·적용 결과 — 2026-10-27

## 결론
기존 AgentBuilder에는 일·주 단위 예약과 서버 트리거가 이미 있다. 생성 도구가 타이머 코드를 매번 만드는 구조가 아니라, 저장된 schedule 설정을 고정 서버 코드가 읽어 실행한다. 이번에는 DST 시간 계산 결함을 수정하고 실제 서버→작업 선점→Python 실행→CSV 정답 대조를 검증했다. 배포·장기 운영·팀 DB 통합은 미완료다.

## 실제 경로
사용자 요청 → 주기/요일/시각/시간대/관리 자료 확인 → 초안 검증·사용자 확인·버전 저장 → 예약 별도 활성화 → Node 서버 시작 시 scheduler 시작 → 즉시 및 5초 간격 tick → recover_runs → claim_due → scheduled_run → 등록 도구 실행 → 결과/실행 기록 저장.

- 서버 진입: Frontend/server/index.ts 의 startBuilderScheduler.
- 트리거: Frontend/server/agent-builder-scheduler.ts 의 startBuilderScheduler/createBuilderScheduleRunner.
- 시간 계산/활성화/선점: Backend/agent_builder/scheduling.py 의 next_occurrence/configure/activate/claim_due.
- 실행과 heartbeat: Frontend/server/agent-builder.ts, Backend/agent_builder/worker.py 및 run lifecycle.
- generic schedule.ts는 다른 흐름용이다. 그쪽 cron/월간 지원을 AgentBuilder 지원이라고 설명하면 안 된다.

## 예시와 사용자 확인
“매주 월요일 오전 9시”는 frequency=weekly, weekday=0, hour=9, minute=0, timezone=Asia/Seoul. 2026-10-27 기준 다음 시각은 2026-11-02 09:00 KST = 00:00 UTC다.
되묻기에서는 요일·시각·시간대·관리 자료·결과 위치를 확인한다. ‘해당 시간’처럼 불명확한 값은 임의 확정하지 않는다. 저장만으로 활성화하지 않으며 activate 호출이 따로 필요하다. 현재 활성화는 관리 표 자료를 요구한다. 예약 생성이 임시 도구 생성 동의가 되지는 않는다.

## 이번 수정
1. DST 종료 후 두 번째 같은 현지 시각에서 과거 UTC 시각을 다음 실행으로 반환하던 비교를 UTC 절대 시각 비교로 변경.
2. 기존 동작을 유지하며 scheduler tick을 주입 가능한 함수로 분리해 중복 tick/실패 기록 검사 추가.
3. 주간 KST 변환, 장기 중지 후 한 건으로 합치는 동작, DST 회귀 검사 추가.

## 검증 수치
- Python 전체 543개 통과, 선택적 SVM 1개 제외. scheduling 전용 13개 통과.
- 영향 Node 검사 81개 통과. TypeScript 및 빌드 통과.
- 실제 주간 설정 + 시험용 다음 시각 15초 후로 조정 → 실제 Node 5초 polling → SQLite → worker → 표 필터 → CSV 결과 A/B 일치, 모델 호출 0회.
- 해당 1회: 예약→작업 생성 13.720초, 예약→실행 기록 생성 18.807초. 전체 시험 42.756초. 장기 평균/P95가 아니다.
- 별도의 schedule_test 실행 및 서버 시작 전에 이미 기한이 지난 주간 실행도 결과 일치. 첫 실행 준비 중 스크립트 경로 오류와 지연 시작 기록을 보존했고 정상 기동 시험과 섞지 않았다.
- 전체 검사는 같은 컴퓨터의 기존 의존성을 재사용했다. 신규 설치 검증은 아니다.

## 운영에서 확정해야 할 정책
1. 상시 서버·프로세스 재시작·지속 저장소: 브라우저는 꺼도 되지만 서버가 꺼져 있으면 실행 안 됨. 실제 운영 서비스에 배포한 상태가 아님.
2. 자료 갱신: 현재 관리 자료를 재사용. 다음 주 새 파일/API 자료 자동 수집은 없음. source_upload 교체나 팀 자료 갱신 규격 필요.
3. 늦은 실행: 중지 기간의 모든 회차를 재생하지 않고 한 건으로 합치고 다음 미래 시각으로 이동하는 현재 동작을 사용자에게 안내. 사용자별 정책 설정 UI는 없음.
4. 겹침: Node runner는 직렬 실행. 긴 작업이면 다음 예약 지연. 5초 scan은 정시 시작 보장 아님. 중단·사용자 확인 대기 작업의 후속 운영 정책도 합의 필요.
5. 공동 DB: 동일 SQLite 파일의 슬롯 유일성/트랜잭션·lease로 선점. 서로 다른 서버의 DB 복사본은 중복 방지 공유 안 됨. RDS 연결 시 같은 작업키·원자 선점·복구 규격 구현 필요. 외부 전송 exactly-once는 보장하지 않음.
6. 지원 판정 연결: condition_readiness는 앱 daily/weekly 예약을 알고 있다. 새 optional capability_judgement v3는 Tool 카탈로그 중심이며 native 예약 정보를 명시하지 않는다. 기본 off 유지. enforce를 서비스에 연결하기 전 앱 예약 능력 전달과 예약 전용 실제 모델 평가가 필요하다. 예약 기능을 새 도구 제작 대상으로 판정하지 않도록 해야 함.

## 공식 근거 대조
- Node timers는 콜백 실행 시각의 정확성을 보장하지 않는다: https://nodejs.org/api/timers.html
- Python zoneinfo의 DST fold/UTC 변환: https://docs.python.org/3.13/library/zoneinfo.html
- SQLite는 동시 쓰기 하나 및 BEGIN IMMEDIATE 트랜잭션을 제공: https://www.sqlite.org/lang_transaction.html
공식 문서는 구현 원리 근거이며 A4B 운영 안정성 자체를 입증하는 근거는 아니다.
