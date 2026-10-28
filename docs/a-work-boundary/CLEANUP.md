# 소스·파일 정리 후 동작 검증 — 2026-10-27

## 결론
승인한 정리 목록을 반영했다. 미사용 import264개/18파일과 중복 조건1곳 제거, 문서·표 읽기 공통화, 시제품 구현3파일 이동, 중간 DB/로그8개 대상 원본 보관 후 작업 폴더에서 제거. 기존 실행 진입점과 P1 차단 동작을 유지했다.

## 처리 내역
- import264개 제거. workflow_runtime.create_deep_agent는 테스트 호환 이름이므로 유지.
- supervisor의 두 번째 unsupported 조건 제거. 이전 감사에서 선택 상태160/160 동등성을 확인했고 현재 전체 회귀도 통과.
- executors/_read_source.py로 공통 읽기 이동. read_document.execute/read_table.execute의 등록 경로·인자·반환/예외 유지.
- request_judgement.py·예제 JSON·binding_repairs.py 구현은 agent_builder/experiments로 이동. 기존 모듈2개는 명시적 재노출만 하는 호환 전달 모듈로 유지. 중복 구현 아님.
- 관련 테스트와 DIRECT_PLANNER.md는 새 경로로 연결. 과거 import의 함수 동일성/예제 로딩도 검사.
- legacy_db/legacy_final_db/legacy_release_db 3폴더와 initial/new/expanded/combined 검사 기록5파일을 보관 ZIP으로 옮긴 뒤 작업 폴더에서 제거.
- 게이트·등록도구·원본 검증기/파서 사본·테스트·legacy_ready_snapshots 유지.

## 검증 결과
| 검사 | 결과 |
|---|---:|
| 전체 Python | 604 통과, 선택적 SVM1개 제외 |
| P1 회귀 | 전체604개 안에 기존45개 포함, 중복 합산 안 함 |
| Node 기존 검사 | 81 통과 |
| 타입·프론트/서버 빌드 | 통과 |
| API handler→실제 Python worker/graph/SQLite | 10/10 |
| 이전 ready 체크포인트 복사본 재개 | 6/6 |
| 변경 전/후 문서·표 읽기 비교 | 22/22 결과·오류 일치 |
| 실제 Node 예약→worker→표 처리→CSV | 성공, 결과 A/B 일치 |
| 기존 import 호환 | 함수 객체 동일·예제 읽기 성공 |
| 원본 보관 ZIP | payload 648개 해시 일치, SQLite18개 integrity 통과 |

읽기 비교22건은 기본·실행 자료 우선·다른 종류 자료·설정 우선·외부 입력·미첨부·다중 자료·없는 자료·잘못된 binding·다른 외부 입력·교차 도구 입력을 두 실행기에서 비교했다.
예약 검사는 합성 표와 시험용 next_at15초 가속을 사용했다. Node timer와 실제 worker/SQLite/등록 도구/CSV 저장을 거쳤다. 장기 운영·실제 주간 경과 시험은 아니다.
API와 생성/판정 검사는 추론을 fixture로 통제했다. 새 실제 모델/유료 API 호출0회. 이전 모델 품질 점수나 모델 응답 속도 개선을 주장하지 않는다.

## 동일성 보호
compatibility.json의 7개 주요 소스(graph, capability_gate, capability_check, save, storage, gate tests, registry)는 정리 전 해시와 동일하다. worker/validation의 import·supervisor의 중복 분기만 정리했으며 게이트 의미를 변경하지 않았다.
import 헤더의 빈 줄 정리는 AST 동일성을 확인했다. 읽기 실행기의 동작은 정리 전 실제 함수와 별도 비교했다.

## 위치와 복구
정리 전 소스와 제거한 중간 기록은 output/archive/A4B_정리전_원본보관_20261027.zip에 보존했다. 원본 해시·DB integrity 검사 후 제거했다. 서비스 data/키/환경/모델 가중치는 건드리지 않았다.
과거 연구 스크립트가 이전 JSON 파일 경로를 직접 읽어 소스 해시를 수집하면 experiments/의 새 경로를 사용하거나 보관본에서 재현해야 한다. 이전 Python import는 호환 모듈로 유지한다.

## 범위
현재16개 앱 도구 기준. 62개 도구·실파서/RDS·브라우저 클릭·실운영/다중 사용자·새 설치 검증은 별도. 기존 의존성을 재사용했다. 기본 모드 변경·학습·배포·커밋/push 없음.
압축 해제한 정리본의 추가 검사와 최종 해시 검증은 PACKAGE_VALIDATION.json/validation/에 포함한다.
