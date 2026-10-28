# 지원 판정 모순 검사 보완 — 2026-10-28

## 수정 전 문제
응답 schema는 clarify와missing_actions의동시출력을금지했지만,Python실행검사는clarify의질문유무만확인했다. 따라서의도적으로모순된JSON을주입하면clarify로받아질문경로로보냈다. 기존gate가생성·저장은막았지만부족기능안내와질문사이의분류일관성이부족했다.

## 수정
- capability_judgement.py: 공통validate_assessment_result추가. existing_tools는실제도구필수/미정·부족항목없음,clarify는질문필수/부족동작없음,missing_capability는부족동작필수/질문없음검사. manual_review는실제겹치는사유둘을보존가능.
- 최초응답/기존미지원재검토응답/앱지원기능정규화후모두같은검사. 공백사유·빈항목·중복/미등록도구·원문근거도실행시확인.
- nodes/capability_check.py: 모르겠어/보류/자료연결안내로이전clarify를재사용할때도재검사. 과거모순된판정을재사용할수없음.
- 모순은AssessmentConflictError로구분→system_error/validation_failed/stop. 업무질문을다시만들거나도구제작으로자동전환하지않음. 원래판정과미정/부족리스트는assessment_conflict에진단정보로보존. 미해결정보를지워정상판정으로바꾸지않음.
- 진단정보는실행허가가아님. 일반confirm/직접save/DB재접속과기본off변경후에도차단. 새로운입력으로일관된판정을받으면정상생성·승인·저장으로회복가능.
- 확정할수있는모순발생추론호출수1/2회,과거판정재사용검사0회를보존. 실제LLM호출시험은아님.
- 조건모듈의기존겹침우선순위는변경하지않음. DB장애/보류/미지원대표상태와미정조건·부족동작의동시보존검사통과.

## 검증
새회귀55개(32개판정조합포함)는전체Python666개에포함. 별도중복합산하지않음. optional_svm_hints1개제외. Node선택82개,타입/빌드통과.
NodeAPI핸들러→실제Python subprocess→graph/SQLite검사13/13. 기존10개+모순중단/업무질문미전환/confirm차단3개. 추론fixture,실제HTTP listener나브라우저UI검사는아님. 기존정상답변·보류·도구제작선택흐름회귀통과.

## 변경 파일
1. Backend/agent_builder/capability_judgement.py
2. Backend/agent_builder/nodes/capability_check.py
3. Backend/agent_builder/test_capability_assessment.py
4. docs/clarification-handoff/provenance.json
5. docs/a-work-boundary/ASSESSMENT_CONSISTENCY.md

기존미추적.ai/폴더는변경하지않았으며이번수정대상이아님.
기본off/enforce설정·프런트선택UI·임시도구등록/실행연결·파서는변경하지않음. 새의존성없음. 커밋/push없음. 이전커밋14ed741에는이수정이포함되지않으므로사용자가추가커밋필요.
이번결과는모순응답의런타임처리보완이며Qwen자연어분류정확도향상이나실서비스전체통합완료의증거가아님.
