# 서비스 미연결 시제품

request_judgement.py·예제 JSON과 binding_repairs.py의 실제 구현을 이곳으로 옮겼다. 서비스 graph는 이 패키지를 직접 호출하지 않는다.
권장 import: agent_builder.experiments.request_judgement / agent_builder.experiments.binding_repairs.
기존 agent_builder.request_judgement / binding_repairs는 과거 실험의 import 호환을 위한 전달 모듈이다. 중복 구현은 없다.
예전 연구 스크립트가 파일 해시를 얻기 위해 이전 JSON 경로를 직접 읽는 경우에는 experiments/ 경로로 조정하거나 정리 전 보관본을 사용해야 한다. 과거 기록·스냅샷은 변경하지 않았다.
