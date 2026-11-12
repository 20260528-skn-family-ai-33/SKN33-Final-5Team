# 오케스트레이션 평가 증거

**H18 통합 판정 보류.** 당시 측정본은 뉴욕 현지 시각과 서머타임(DST)을 정답으로 사용했다. 이후 main의 PR #90이 예약 시간대를 서울로 고정했으므로, 이 보고서의 H18 통과 건수는 해당 과거 측정본에만 적용한다.

보고서의 수치는 각 원본에 기록한 앱·평가 하니스·동결 사례의 해시에 해당한다. PR 통합 시점의 체크아웃에서 새로 측정한 수치는 별도 실행으로 기록한다. 이전 작업 트리와 모든 후보 소스 사본은 측정기의 재분석 입력에 포함되지 않는다.

[공개 파일별 SHA-256 목록](../runtime/orchestration-published-evidence.json)은 최초 로컬 파일과 공개 사본의 바이트 해시 및 처리 종류를 기록한다. JUnit 결과에서는 호스트 이름을 `local-runner`로 바꿨고, 모델 목록에서는 해당 평가에 사용한 모델만 남겼다. 공개된 합성 사례에는 재생에 쓰는 세션 ID와 질문 ID가 포함돼 있다. 모델 사용량·단계별 시간·워크플로우·정답·검증 판정과 그 시점 코드의 해시는 유지했다.

- [선별 9B 실험 결과표](../runtime/planner-capability-9b-experiment/measured-results.md) 및 [H21 첫 사례 전후 비교](../runtime/planner-capability-9b-experiment/h21-first-comparison.json)는 같은 워커의 실측 파일을 사용한다. 전후 의미 판정과 호출·시간은 [비교기](../runtime/planner-capability-9b-experiment/compare.py)로 다시 확인할 수 있다.
- 저장 응답 재생에는 [52개 중 선택한 정상 3건](../runtime/generalization-focused/replay-fixtures/legacy52.selected.json) 및 [35개 중 선택한 9건](../runtime/generalization-focused/replay-fixtures/independent35.selected.json)을 사용한다. 로컬 원본 전체의 SHA 및 선택한 사례 ID는 해시 목록에 있다. 선택 자료로 원래 전체 평가의 성공률을 다시 산출할 수는 없다.
- 현재 checkout의 오프라인 회귀 및 새로운 9B 실측 명령은 [Planner 및 Capability 보고서](a4b-planner-capability-9b-latency.md)에 있다. Windows 명령은 `Backend/.venv-agent/Scripts/python.exe`에 워커 패키지가 설치된 환경을 가정한다. 다른 환경은 본인의 워커 경로로 바꾼다.
