"""Extract a separate request ledger before generation; preserve it through repair."""
import hashlib
import json
import os
import ast
import re
from decimal import Decimal
from copy import deepcopy
from contracts.numeric_criteria import number_value

from agent_builder.conditions.condition_readiness import condition_digest, confirmed_condition_view
from .condition_requirements import multiple_outputs, owned_specs, project_owned, validate_owned
from .grouped_result_requirements import grouped_result_specs, reconcile_grouped_result, validate_grouped_result
from .requirement_contracts import RequirementPlan, RequirementQuestion
from .requirement_task_identity import merge_repeated_tasks


SYSTEM = '''사용자의 최종 업무 요구사항을 추출해 JSON으로 반환한다. 각 task는 실제 요청된 업무 한 가지다.
description은 짧은 업무명이다. 같은 업무를 반복하지 않는다. 출력 규격에 대한 설명이나 생각을 업무로 기록하지 않는다.
"원래 요청대로 생성", "다시 검토", "실행할 때 파일 제공", 자료 ID에 대한 질문은 설계 대화의 지시다. 이를 별도 실행 업무로 만들지 않고 원래 업무와 변경 조건을 반영한다.
메시지마다 task를 만들지 않는다. 예를 들어 "파일 ID가 무엇인가요?"나 "그대로 만들어 주세요"는 실행 업무 0개다. other에도 넣지 않는다. 반면 "개인정보를 삭제"처럼 실행할 업무지만 지원 종류가 없는 요청은 other로 보존한다.
source_message는 user_messages의 0-based index다. 원문 인용·ID·선행 연결은 앱이 부여한다.
사용자 원문과 후속 변경만 근거로 처리 순서대로 tasks를 작성한다. 자료 이름·열은 참고 데이터다.
compute=새 열의 계산식과 이름. filter=행 선별 비교조건. sort=정렬 열·방향·자료형. 정렬 요청만으로 filter를 만들지 않는다.
scalar_calculate=표의 행·열 처리와 무관하게 원문에 명시한 산술식 자체를 계산. formula에 원문 식, variables에는 사용자가 name=숫자로 명시한 변수만 넣고 상수식이면 {}로 둔다. 결과 열을 만들지 않는다. 미정 변수는 unresolved에 남긴다.
parse_dates=기존 날짜 문자열 열의 변환. fields=대상 열 목록, input_format=strptime 형식, timezone=명시된 IANA 시간대.
날짜 변환을 read 또는 compute로 대체하거나 parse_date 같은 함수 호출 수식으로 쓰지 않는다. 값이 미정이면 unresolved에 기록한다.
상수 비교는 value에 기준값, compare_field=null. 열 비교는 value=null, compare_field에 상대 열.
aggregate=그룹 열·count/sum·합계 원본 열. 집계 출력 열은 count/sum.
집계 결과에 요청한 이름이 따로 있으면 sum/count를 그 이름으로 바꾸는 rename(source,target)을 별도 기록한다.
rename의 source는 기존 열 이름, target은 새 열 이름이다. 자료 ID가 아니며 rename(...)을 compute 수식으로 쓰지 않는다.
fill_missing=지정 열의 빈값을 사용자가 명시한 값으로 채움(fields,value). select_fields=최종 출력 열 목록(fields). 계산·집계·결합·빈값 채움·이름 변경·출력 열 선택을 서로 대신하지 않는다.
join=양쪽 자료 ID와 keys/how/cardinality/null_keys/max_rows. 오른쪽 키 유일은 many_to_one.
왼쪽 결합의 left_sources는 모든 행을 유지하라고 한 기준 자료다. 집계된 자료를 오른쪽에 붙일 때도 이 방향을 바꾸지 않는다. 최대 행 수가 미정이면 unresolved에 적는다.
policy=일반 표의 빈값/중복 행 처리. 결합의 키 유일성·null 키·최대 행 수는 join의 필드값이다.
export=요청한 파일 형식. read=명시적으로 읽는 자료 한 개. 자료 ID는 실제 목록에서 선택한다.
후속 답변에서 바꾸지 않은 조건도 유지한다. 필요한 값이 미정이면 unresolved에 적고 추측하지 않는다.
schedule=hourly/daily/weekly/monthly 실행 메타데이터. schedule에 실제 주기·시간대·분을 넣고 daily/weekly/monthly에는 시, weekly에는 요일, monthly에는 일자 또는 말일을 기록한다. 미정이면 unresolved에 남긴다.
document_analysis=문서 원문을 읽고 요약·누락·모순 분석·근거와 수정 제안 같은 서술형 결과를 작성하는 업무. 원래 분석 요청 전체가 실행 지침으로 전달된다. 필드만 추출하는 extract_fields와 구분한다.
human_review=생성한 결과를 사람이 실제 실행 중 확인하도록 대기하는 업무. 설계 대화에서 재검토하라는 말은 human_review가 아니다.
개인정보 제거·분기·기타 위 종류로 표현할 수 없는 업무나 추가 조건은 other로 남긴다. 여러 분석 관점은 같은 document_analysis 한 건에 유지한다.
모든 사용자 요구를 기록한 뒤 tasks를 끝내고 unresolved를 작성한다. JSON 이외에는 출력하지 않는다.'''
SYSTEM += '''\n서비스 연결과 인증은 앱 서버가 관리한다. 계정 ID·액세스 토큰·비밀키는 사용자 업무 조건이 아니며 unresolved로 요구하지 않는다.
service_connections는 연결 여부의 메타데이터일 뿐이며 인증값은 받거나 추측하지 않는다. 연결되지 않았어도 실행 전에 서비스 연결에서 준비할 수 있다.
unresolved에는 사용자가 실제로 정해야 하는 업무 조건을 자연스러운 질문으로 작성한다. 내부 필드 이름만 출력하지 않는다.
사용자가 요구하지 않은 요약 형식 선호는 필수 조건으로 만들지 않는다. clarification_answers는 실제 질문과 사용자 답변이며 원래 요청과 함께 해석한다.'''
SYSTEM += '\nGmail 조회 건수가 명시되지 않으면 앱의 실행 한도인 최대 100건을 사용할 수 있다. 이는 실행 한도이며 업무 필터의 추정값이 아니다. 사용자가 건수를 정했다면 그 값을 보존한다.'
SYSTEM += '\n예약 시간대는 앱 고정 설정인 서울 표준시(Asia/Seoul)다. 원문에 시간대가 없어도 Asia/Seoul로 기록한다. 시간대는 미정 조건이나 질문으로 만들지 않으며, 다른 시간대 요청도 서울 표준시로 실행한다.'
SYSTEM += '''\nunresolved의 각 항목은 문자열이 아닌 질문 객체 {key,text,group,reason,options,response,required}다.
각 요청사항에서 고객이 결정할 조건을 개별 질문으로 나눈다. group은 관련 요청사항의 짧은 제목이고 key는 같은 조건을 식별할 안정적인 키다.
text에는 고객이 이해할 완전한 질문만 작성하며 내부 변수 이름·토큰·계정 ID를 묻지 않는다.
reason에는 이 답이 없으면 어떤 업무 설정을 결정할 수 없는지 한 문장으로 설명한다. 질문이나 내부 필드명을 반복하지 않는다.
user_messages와 clarification_answers에서 이미 확인한 값은 보존하고 같은 조건을 다시 묻지 않는다. 후속 답변은 이전 원문과 함께 해석한다.
"더 구체적으로 설명해주세요", "필요한 정보를 알려주세요" 같은 통짜 질문은 금지한다. 실제로 누락된 조건 하나와 필요한 이유를 명시한, 답할 수 있는 질문만 작성한다.
선택 가능한 조건은 response.type=single_choice, 여러 항목 선택은 multi_choice로 선언하고 options에 실제로 고를 항목을 두 개 이상 작성한다.
숫자 조건은 response.type=number로 선언하고 options는 비운다. minimum/maximum/step/unit은 실제 업무·도구 계약에 근거가 있을 때만 쓴다.
실행 시각은 response.type=time, key=schedule.time으로 선언하고 09:00처럼 HH:MM 형식으로 시와 분을 한 번에 받는다. 시간은 정수형 number로 받거나 hour/minute 질문으로 쪼개지 않는다. 수량·금액 등 실제 숫자는 number를 유지한다.
자유 서술이 필요한 조건만 response.type=text로 선언하고 options는 비운다. 선택형이나 수치 조건을 긴 자유 답변 질문 하나로 합치지 않는다.
실행 일정·결과의 내용 구성·파일 형식·전달 방식은 서로 다른 결정이다. 미정인 항목은 각각 별도의 질문과 적절한 선택지로 제시한다.
선택지는 available_capabilities에 등록된 실제 기능 안에서 작성한다. 외부 발송이나 파일 형식을 도구 계약 확인 없이 가능하다고 제안하지 않는다.
처리 내용을 새로 지어내는 선택지는 만들지 않되, 등록 기능으로 가능한 형식·처리 방식은 고객이 고를 수 있게 한다.
선호를 묻는 질문은 required=false로 두며 필수 조건처럼 막지 않는다. 단, 선택하지 않은 선호를 임의로 확정하지 않는다.
질문에 대한 batch 답변은 clarification_answers의 question_id/key/text/value 연결을 따라 해당 조건만 갱신한다.
clarification_answers에서 skipped=true와 value=null은 고객이 선택적인 선호 질문을 건너뛴 기록이다. 해당 값·형식을 임의로 확정하거나 같은 선택 질문을 필수 조건으로 다시 묻지 않는다.
구체적인 업무를 아직 기록할 수 없으면 tasks=[]로 두고 확인할 질문을 unresolved에 작성한다. tasks와 unresolved가 둘 다 빈 응답은 허용하지 않는다.'''


from .question_language import KOREAN_QUESTION_INSTRUCTION
SYSTEM+='\n'+KOREAN_QUESTION_INSTRUCTION
SYSTEM += '''\nconversation의 직전 질문과 pending_questions를 이용해 짧은 답변의 대상을 해석한다.
previous_requirements는 이전 후보이며 사용자 원문보다 우선하지 않는다. 새 답변이 바꾸지 않은 조건은 유지하고 최신 변경은 반영한다.
unresolved에는 사용자가 결정해야 하는 누락 조건만 구체적인 한국어 질문으로 적는다. 내부 오류·분류·스키마 설명은 넣지 않는다.
필수 값이 모두 명시됐으면 unresolved=[]로 둔다. 이대로 진행·저장할지 같은 요청 재확인·승인은 생성 후 앱 검토 단계의 역할이며 누락 조건으로 넣지 않는다.
명시된 값이나 temporal_context에서 계산된 날짜를 다시 묻지 않는다. 답변이 불명확하면 추측해 확정하지 않는다.'''
SYSTEM += '''\nconfirmed_conditions.slots는 사용자 근거 검증을 마친 최종값이며 앱이 관련 업무를 자동으로 작성한다.
확정 조건에서 파생된 업무는 tasks에 다시 작성하거나 other로 바꾸지 않는다. 추가 업무가 없으면 tasks=[]다.
unresolved는 사용자 결정이 필요한 나머지 조건만 key,text,group,reason,options,response,required 구조로 작성한다.
key는 영문 소문자와 점으로 구성한다(예: delivery.recipient, compute.formula, compute.output_column).
조건 장부와 같은 의미일 때는 동일한 키를 사용한다(예: duplicate.policy, missing.policy, schedule.weekday, output.format).'''


def _customer_question_plan(plan,questions):
    """Retain the real partial work, never app-managed credential gaps."""
    by_text={question['text']:question for question in questions}
    unresolved=[];retained=set()
    for original in plan['unresolved']:
        text=original if isinstance(original,str) else original['text']
        if text in by_text:
            unresolved.append(original);retained.add(text)
    unresolved.extend(question for text,question in by_text.items() if text not in retained)
    return {**plan,'unresolved':unresolved}


def _clarification(state, plan, fingerprint, calls):
    """Keep the partial ledger and question identities through typed answers."""
    from .requirement_clarification import business_questions,clarification_reply
    questions,unknown=business_questions(plan['unresolved'],state)
    if unknown:raise ValueError('고객에게 표시할 구체적인 질문을 구성하지 못했습니다.')
    plan=_customer_question_plan(plan,questions)
    pending=clarification_reply(state,questions,calls)
    if pending is None:
        if not plan['tasks']:raise ValueError('요청한 업무 목록을 구성해야 합니다.')
        return _completed(state,'template' if state.get('condition_route')=='template_plan' else 'plan',
                          {**plan,'unresolved':[]},fingerprint,calls)
    return {**pending,'requirement_route':'await_input','requirement_ledger':plan,
            'requirement_fingerprint':fingerprint,'requirement_digest':plan_digest(plan)}


def ledger_fingerprint(state):
    from agent_builder.capabilities.capability_gate import input_fingerprint
    # Version seals the interpretation contract as well as the input/catalog.
    meaning = state.get('request_meaning_digest') or ''
    ledger = state.get('condition_ledger') or {}
    authority = (condition_digest(ledger), ledger.get('validation_digest')) if state.get('condition_enforced') else None
    return plan_digest(['a4b.requirements.v9.grouped-result', input_fingerprint(state), meaning, authority])


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def grounded_schedule(state):
    from contracts.models import ScheduleSpec
    from agent_builder.conditions.condition_readiness import explicit_policies, source_catalog
    from workflow_engine.scheduling.recurrence import schedule_errors
    written = explicit_policies(state, source_catalog(state)[0])
    slots = {key.split('.', 1)[1]:item['value'] for key,item in written.items()
             if key.startswith('schedule.') and item.get('status') == 'specified'}
    from .requirement_clarification import schedule_facts
    slots.update({key.split('.',1)[1]:value for key,value in schedule_facts(state).items()})
    spec = dict(frequency=slots.get('frequency'), timezone=slots.get('timezone'),
                hour=slots.get('hour'), minute=slots.get('minute'), weekday=slots.get('weekday'),
                monthday=slots.get('monthday'), month_end=slots.get('month_end', False))
    if spec['frequency'] == 'hourly':spec['hour'] = None
    if spec['frequency'] != 'weekly':spec['weekday'] = None
    if spec['frequency'] != 'monthly':spec.update(monthday=None, month_end=False)
    elif spec['month_end']:spec['monthday'] = None
    if schedule_errors(spec):raise ValueError('예약 주기·시각·시간대·월간 일자의 원문 조건이 충분하지 않습니다.')
    return ScheduleSpec.model_validate(spec).model_dump()


def validate_plan(raw, state):
    plan=RequirementPlan.model_validate(raw).model_dump(mode='json')
    from workflow_engine.scheduling.policy import timezone_question
    plan['unresolved']=[question for question in plan['unresolved']
                        if not timezone_question(question if isinstance(question,dict) else {'key':question,'text':question})]
    from .execution_mode import filter_execution_questions
    plan['unresolved']=filter_execution_questions(plan['unresolved'],state)
    if not plan['tasks'] and not plan['unresolved']:
        raise ValueError('이미 확인한 조건이나 현재 필요하지 않은 예약 질문만 반환했습니다. 요청한 실제 업무 목록을 추출하세요.')
    from .requirement_contracts import validate_unanswered_questions
    validate_unanswered_questions(plan,state)
    json.dumps(plan,allow_nan=False)
    users=[m['content'] for m in state.get('messages',[]) if m['role']=='user']
    assets={a['id'] for a in state.get('resources',[])}
    ids=[t['id'] for t in plan['tasks']]
    if len(set(ids))!=len(ids):raise ValueError('요구사항 ID가 중복됩니다.')
    all_ids=set(ids)
    text='\n'.join(users)
    confirmed = confirmed_condition_view(state)
    if state.get('condition_enforced') and state.get('condition_ledger') and not confirmed:
        raise ValueError('확정 조건의 현재 턴·버전·근거를 다시 검증해야 합니다.')
    expected = owned_specs(confirmed, state)
    validate_owned(plan, expected, state)
    if confirmed and any(isinstance(question, str) or question['key'] in confirmed['slots']
                         for question in plan['unresolved']):
        raise ValueError('확정 조건과 대조되지 않은 질문이 요구사항에 남아 있습니다.')
    from .compute_grounding import validate_compute_grounding
    validate_compute_grounding(plan, state)
    from agent_builder.conditions.condition_readiness import _amounts
    numbers=_amounts(text)
    for value in re.findall(r'(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?',text):
        try:numbers.add(number_value(value))
        except ValueError:pass
    for task in plan['tasks']:
        index=task['source_message']
        if index>=len(users) or not task['quote'].strip() or task['quote'] not in users[index]:
            raise ValueError('요구사항의 사용자 원문 근거를 확인하지 못했습니다.')
        if (task['id'] in task['depends_on'] or len(set(task['depends_on']))!=len(task['depends_on'])
                or not set(task['depends_on'])<=all_ids):
            raise ValueError('요구사항 ID·선행 업무 관계가 잘못됐습니다: '+
                             json.dumps({'id':task['id'],'depends_on':task['depends_on']},ensure_ascii=False))
        spec=task['spec']
        if spec['kind']=='document_analysis' and spec['request']!=task['quote']:
            raise ValueError('문서 분석 지침은 해당 사용자 발화 전체를 보존해야 합니다.')
        if spec['kind']=='fill_missing' and isinstance(spec['value'],(int,float)) and not isinstance(spec['value'],bool):
            if Decimal(str(spec['value'])) not in numbers:raise ValueError('빈값 대체 숫자가 사용자 원문에서 확인되지 않습니다.')
        if spec['kind']=='parse_dates':
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            if len(set(spec['fields']))!=len(spec['fields']):raise ValueError('날짜 변환 대상 열이 중복됩니다.')
            try:ZoneInfo(spec['timezone'])
            except (ZoneInfoNotFoundError,ValueError) as error:raise ValueError('날짜 변환 시간대를 확인하세요.') from error
        if spec['kind']=='schedule' and 'schedule' not in expected and spec['schedule']!=grounded_schedule(state):
            raise ValueError('예약 요구사항이 사용자가 명시한 주기·시각·일자와 다릅니다.')
        if spec['kind'] in ('gmail_summary', 'gmail_scheduled_summary'):
            from .gmail_summary import gmail_summary_request
            gmail=gmail_summary_request(state)
            if not gmail:
                from .gmail_requirement_grounding import validate_general_gmail_task
                validate_general_gmail_task(task,state,schedule_elsewhere=any(t['spec']['kind']=='schedule' for t in plan['tasks']))
            elif (spec['max_results']!=gmail['count']
                    or task['quote']!=gmail['request'] or spec['request']!=gmail['request']
                    or spec.get('summary_instructions')!=gmail.get('summary_instructions')):
                raise ValueError('Gmail 조회 건수·요약 요구를 사용자 원문에서 확인할 수 없습니다.')
            if gmail and spec['kind']=='gmail_scheduled_summary':
                if spec['schedule']!=gmail.get('schedule') or spec['query']!=gmail.get('query'):
                    raise ValueError('Gmail 예약 시각·조회 조건이 사용자 요청과 다릅니다.')
            elif gmail and gmail.get('schedule'):
                raise ValueError('Gmail 예약 요구사항의 실행 시각과 조회 기간이 빠졌습니다.')
            elif gmail and spec.get('query') != gmail.get('query'):
                raise ValueError('조회 기간·필터가 해석된 사용자 요구와 다릅니다.')
        if spec['kind'] in ('extract_fields','receipt_items') and (len(set(spec['fields']))!=len(spec['fields']) or
                any(not field.strip() or field not in task['quote'] for field in spec['fields'])):
            raise ValueError('추출할 항목을 사용자 원문에서 확인할 수 없습니다.')
        if spec['kind']=='compute':
            from workflow_engine.table_formula import formula_columns
            formula_columns(spec['formula'])
            constants=[n.value for n in ast.walk(ast.parse(spec['formula'],mode='eval')) if isinstance(n,ast.Constant)]
            if any(abs(Decimal(str(v))) not in {abs(n) for n in numbers} for v in constants):
                raise ValueError('계산식의 숫자가 사용자 원문에서 확인되지 않습니다.')
        if spec['kind']=='scalar_calculate':
            from .scalar_requirement import validate_scalar_requirement
            validate_scalar_requirement(spec, task['quote'], users)
        for field in ('file_ids','left_sources','right_sources'):
            if not set(spec.get(field,[]))<=assets:
                raise ValueError('요구사항에 실제 연결 자료가 아닌 ID가 있습니다.')
        if spec['kind']=='filter':
            for f in spec['filters']:
                unary=f['operator'] in ('is_empty','not_empty')
                if not unary and ((f['value'] is None)==(f['compare_field'] is None)):
                    raise ValueError('선별 조건은 기준 값 또는 비교 열 중 하나만 가져야 합니다.')
                if f['compare_field']==f['field']:
                    raise ValueError('같은 열을 자기 자신과 비교하는 조건을 요청 근거로 확정할 수 없습니다.')
                if f['value_type']=='number' and f['value'] is not None:
                    number=number_value(f['value'])
                    if number not in numbers:raise ValueError('선별 기준 숫자가 사용자 원문에서 확인되지 않습니다.')
                    if isinstance(f['value'],str):f['value']=str(number)
        if spec['kind']=='aggregate' and ((spec['aggregation']=='sum') != bool(spec['sum_field'])):
            raise ValueError('합계/건수 요구사항의 원본 열을 확인하세요.')
        if spec['kind']=='policy' and spec['missing_policy'] is None and spec['duplicate_policy'] is None:
            raise ValueError('값이 없는 정책 항목은 사용자 업무가 아닙니다.')
    from .explicit_date_criteria import validate_requested_dates
    from .explicit_table_criteria import validate_explicit_ledger
    validate_requested_dates(plan,state)
    from .processing_guard import validate_group_aggregations
    validate_group_aggregations(plan, state)
    validate_grouped_result(plan, state)
    validate_explicit_ledger(plan,state)
    # Forward references are not a semantic error. Reorder an acyclic ledger
    # without dropping dependencies or rewriting any requested condition.
    remaining=list(plan['tasks']);ordered=[];seen=set()
    while remaining:
        ready=[t for t in remaining if set(t['depends_on'])<=seen]
        if not ready:raise ValueError('요구사항의 선행 업무에 순환이 있습니다.')
        task=ready[0]
        ordered.append(task);seen.add(task['id']);remaining.remove(task)
    plan['tasks']=ordered
    return plan


def extraction_schema(user_count=None,asset_ids=None,*,owned_kinds=()):
    schema=RequirementPlan.model_json_schema()
    schema['properties']['unresolved']['items']={'$ref':'#/$defs/RequirementQuestion'}
    question=schema['$defs']['RequirementQuestion']
    question['required']=list(question['properties'])
    question['properties']['reason']={'type':'string','minLength':1,'maxLength':500,
        'description':'이 답이 없으면 결정할 수 없는 업무 설정을 고객에게 설명합니다.'}
    # The extractor chooses the action and source turn. The application copies
    # that complete request rather than asking a second model to paraphrase it.
    analysis=schema['$defs']['DocumentAnalysis']
    analysis['properties'].pop('request');analysis['required'].remove('request')
    task=schema['$defs']['RequiredTask']
    if owned_kinds:
        kinds = task['properties']['spec']['discriminator']['mapping']
        refs = {kinds.pop(kind) for kind in owned_kinds}
        branches = task['properties']['spec']['oneOf']
        branches[:] = [branch for branch in branches if branch.get('$ref') not in refs]
    # Legacy checkpoint questions may still be strings. New decoder responses
    # identify which value is missing even when the wording changes.
    schema['properties']['unresolved']['items'] = {'$ref': '#/$defs/RequirementQuestion'}
    for field in ('id','depends_on','quote'):
        task['properties'].pop(field)
        task['required'].remove(field)
    if user_count is not None:task['properties']['source_message']['enum']=list(range(user_count))
    base=schema['$defs']['FilterCondition'];variants=[]
    for mode in ('constant','column','unary'):
        branch=deepcopy(base);props=branch['properties']
        props['operator']['enum']=(['is_empty','not_empty'] if mode=='unary' else
                                  ['eq','ne','lt','lte','gt','gte','contains'])
        if mode=='constant':
            props['compare_field']={'type':'null'}
            props['value']={'anyOf':[v for v in props['value']['anyOf'] if v.get('type')!='null']}
        elif mode=='column':
            props['value']={'type':'null'};props['compare_field']={'type':'string','minLength':1}
        else:
            props['value']={'type':'null'};props['compare_field']={'type':'null'}
        variants.append(branch)
    schema['$defs']['FilterCondition']={'anyOf':variants}
    policy=schema['$defs']['Policy'];variants=[]
    for field in ('missing_policy','duplicate_policy'):
        branch=deepcopy(policy)
        choices=branch['properties'][field]['anyOf']
        branch['properties'][field]={'anyOf':[v for v in choices if v.get('type')!='null']}
        variants.append(branch)
    schema['$defs']['Policy']={'anyOf':variants}
    schema['$defs']['Read']['properties']['file_ids']['maxItems']=1
    if asset_ids is not None:
        for definition,fields in [('Read',['file_ids']),('Join',['left_sources','right_sources'])]:
            for field in fields:
                array=schema['$defs'][definition]['properties'][field]
                if asset_ids:
                    array['items']={'type':'string','enum':list(asset_ids)}
                    array['minItems']=1;array['maxItems']=1
                else:array['maxItems']=0
    return schema


def extract_plan(raw,state):
    """Code owns IDs and sequential data dependencies, not the model.

    This version verifies a single sequential processing chain. Independent
    branches/multiple chains require review rather than invented dependencies.
    """
    if (not isinstance(raw,dict) or set(raw)!={'tasks','unresolved'} or not isinstance(raw['tasks'],list)
            or not isinstance(raw['unresolved'], list) or len(raw['tasks']) > 30 or len(raw['unresolved']) > 10):
        raise ValueError('업무 목록 응답 형식을 확인하세요.')
    users=[m['content'] for m in state['messages'] if m['role']=='user']
    for item in raw['tasks']:
        if not isinstance(item,dict) or set(item) != {'source_message', 'description', 'spec'}:
            raise ValueError('업무 ID·선행 연결·원문 인용은 앱 코드가 부여합니다.')
        source=item.get('source_message')
        if type(source) is not int or not 0<=source<len(users) or not isinstance(item['spec'], dict):
            raise ValueError('요구사항의 사용자 발화를 확인하지 못했습니다.')
    from .compute_grounding import reconcile_compute
    raw = reconcile_compute(raw, state)
    confirmed = confirmed_condition_view(state)
    expected = owned_specs(confirmed, state)
    candidates = project_owned(raw['tasks'], expected, state)
    unresolved = []
    for question in raw['unresolved']:
        if not isinstance(question, str):
            question = RequirementQuestion.model_validate(question).model_dump()
            if confirmed and question['key'] in confirmed['slots']:
                continue
        elif confirmed:
            # A legacy free-text candidate cannot prove it is about a new slot.
            # Ask the extractor to supply a key instead of hiding a real user need.
            raise ValueError('확정 조건과 대조하려면 unresolved 항목에 key와 text가 필요합니다.')
        unresolved.append(question)
    candidates = merge_repeated_tasks(candidates,state)
    grouped = grouped_result_specs(state) if not unresolved else None
    if grouped:
        candidates = reconcile_grouped_result(candidates, state, grouped)
    tasks=[];previous=None
    for index,item in enumerate(candidates,1):
        source=item['source_message']
        kind=(item.get('spec') or {}).get('kind')
        # Keep original task provenance for distinct artifacts. A materialized
        # policy or schedule cites the user turn that actually confirmed it.
        if kind in expected and (kind != 'export' or not multiple_outputs(state)):
            source = expected[kind]['source_message']
        if kind=='document_analysis':
            if 'request' in item['spec']:raise ValueError('문서 분석 원문은 앱이 부여합니다.')
            item={**item,'spec':{**item['spec'],'request':users[source]}}
        dependency=[previous] if previous and kind not in ('read','policy') else []
        task={**item,'source_message':source,'id':'r'+str(index),'depends_on':dependency,'quote':users[source]}
        tasks.append(task)
        if kind not in ('read','policy','export') or (grouped and kind == 'read'):
            previous=task['id']
    return validate_plan(dict(tasks=tasks,unresolved=unresolved),state)


def requirement_transport(model, api_key=''):
    from infrastructure.llm.model_factory import LOCAL_MODEL_API_KEY
    if os.getenv('A4B_MAIN_PROVIDER')=='ollama' and api_key in (None,'',LOCAL_MODEL_API_KEY):
        from agent_builder.design.nodes.direct_conversation import OllamaPlanner
        return OllamaPlanner(model,temperature=0)
    class Structured:
        def invoke(self,messages):
            from infrastructure.llm.model_factory import create_chat_model
            llm=create_chat_model(api_key,model,timeout=70,max_retries=0,temperature=0)
            parsed=llm.with_structured_output(self.schema,method='json_schema').invoke(messages)
            return dict(content=parsed.model_dump_json() if hasattr(parsed,'model_dump_json') else json.dumps(parsed))
    return Structured()


def _completed(state, target, plan, fingerprint, calls):
    return dict(requirement_route=target, requirement_required=True, requirement_ledger=plan,requirement_failure=None,
                requirement_fingerprint=fingerprint, requirement_digest=plan_digest(plan),
                pending_questions=[q for q in state.get('pending_questions', []) if q.get('source') not in ('requirement','requirements')],
                confirmed=False, saved_agent_id=None,
                trace=state.get('trace', [])+[dict(node='requirements', label='요청 업무 목록', status='completed',
                     detail=f"원문에서 {len(plan['tasks'])}개 요구사항을 생성 전에 별도로 기록했습니다.", model_calls=calls)])


def make_requirement_check(model, transport=None, mode=None, *, api_key='', error_observer=None):
    setting=mode or os.getenv('A4B_REQUIREMENT_MODE','off')
    if setting not in ('off','enforce'):
        raise ValueError('A4B_REQUIREMENT_MODE는 off/enforce이어야 합니다.')

    def check(state):
        target='template' if state.get('condition_route')=='template_plan' else 'plan'
        from .gmail_summary import gmail_summary_plan
        gmail_plan=gmail_summary_plan(state)
        if gmail_plan:
            plan=validate_plan(gmail_plan,state)
            return dict(requirement_route=target,requirement_required=True,requirement_ledger=plan,requirement_failure=None,
                        requirement_fingerprint=ledger_fingerprint(state),requirement_digest=plan_digest(plan),
                        pending_questions=[q for q in state.get('pending_questions', []) if q.get('source') not in ('requirement','requirements')],
                        confirmed=False,saved_agent_id=None,
                        trace=state.get('trace',[])+[dict(node='requirements',label='요청 업무 목록',status='completed',
                            detail='원문의 Gmail 조회 건수와 본문 요약을 등록 계약으로 기록했습니다.',model_calls=0)])
        if setting=='off' and not state.get('requirement_required'):
            return dict(requirement_route=target,requirement_required=False,requirement_failure=None)
        calls=0
        failure_kind = 'model_output_error'
        try:
            fingerprint=ledger_fingerprint(state)
            confirmed = confirmed_condition_view(state)
            from .scheduled_table_request import scheduled_table_plan
            scheduled = scheduled_table_plan(state, confirmed)
            old=state.get('requirement_ledger')
            if (old and state.get('requirement_fingerprint')==fingerprint
                    and state.get('requirement_digest')==plan_digest(old)):
                try:
                    cached = validate_plan(old, state)
                except (ValueError, KeyError, TypeError):
                    cached = None
                if cached and (scheduled is None or cached == scheduled):
                    if cached['unresolved']:
                        return _clarification(state, cached, fingerprint, 0)
                    return dict(requirement_route=target,requirement_required=True,requirement_failure=None)
            if scheduled is not None:
                # An old opaque task may pass schema checks but differs from this complete projection.
                failure_kind = 'condition_state_error'
                return _completed(state, target, validate_plan(scheduled, state), fingerprint, 0)
            if state.get('condition_enforced') and state.get('condition_ledger') and not confirmed:
                failure_kind = 'condition_state_error'
                raise ValueError('확정 조건의 현재 턴·버전·근거를 다시 검증해야 합니다.')
            from .compute_grounding import missing_compute_packet
            packet = missing_compute_packet(state)
            if packet is not None:
                return _clarification(state, extract_plan(packet, state), fingerprint, 0)
            owned = set(owned_specs(confirmed, state))
            if multiple_outputs(state):
                owned.discard('export')
            active=transport
            if active is None:
                active=requirement_transport(model,api_key)
            active.schema=extraction_schema(len([m for m in state['messages'] if m['role']=='user']),
                                            [a['id'] for a in state.get('resources',[])], owned_kinds=owned)
            from .temporal_context import temporal_context, TEMPORAL_INSTRUCTIONS
            from .request_meaning import sealed_meaning
            payload={'user_messages':[m['content'] for m in state['messages'] if m['role']=='user'],
                     'conversation':state['messages'],
                     'pending_questions':state.get('pending_questions', []),
                     'previous_requirements':state.get('requirement_ledger'),
                     'temporal_context':temporal_context(state), 'request_meaning':sealed_meaning(state),
                     'resources':[{k:a[k] for k in ('id','name','kind','columns') if k in a}
                                  for a in state.get('resources',[])]}
            from .requirement_contracts import question_capability_context
            payload['available_capabilities']=question_capability_context(state)
            from .service_connections import service_connection_context
            payload['service_connections']=service_connection_context(state)
            if state.get('requirement_answers'):
                payload['clarification_answers']=state['requirement_answers']
            from agent_builder.design.execution_mode import execution_mode_context
            if execution_mode_context(state):payload['execution_choice']=execution_mode_context(state)
            if confirmed:
                payload['confirmed_conditions'] = confirmed
            messages=[dict(role='system',content=SYSTEM+'\n'+TEMPORAL_INSTRUCTIONS),dict(role='user',content=json.dumps(payload,ensure_ascii=False))]
            for attempt in range(2):
                calls+=1
                failure_kind = 'system_error'
                response=active.invoke(messages)
                failure_kind = 'model_output_error'
                from .gmail_requirement_grounding import MissingGmailSchedule
                try:
                    if isinstance(response, dict):
                        content = response.get('content')
                        if (response.get('metrics') or {}).get('done_reason') not in (None, 'stop'):
                            raise ValueError('요구사항 추출 응답이 중간에 끝났습니다.')
                    else:
                        content = getattr(response, 'content', None)
                    if not isinstance(content, str):
                        raise ValueError('요구사항 응답 본문이 올바른 문자열이 아닙니다.')
                    plan=extract_plan(json.loads(content),state)
                except MissingGmailSchedule as error:
                    from .requirement_clarification import clarification_reply
                    return {**clarification_reply(state,error.questions,calls),'requirement_route':'await_input'}
                except (ValueError,TypeError,KeyError) as error:
                    if attempt:raise
                    messages=messages+[dict(role='user',content='앞선 추출은 무효입니다. 다음 응답 계약 오류를 해결하여 원래 사용자 요구만 새로 추출하세요. '
                        '조건 값을 추측하거나 사용자 업무를 삭제하지 마세요. 오류: '+str(error)[:400])]
                    continue
                from .requirement_clarification import business_questions, clarification_reply
                questions,unknown=business_questions(plan['unresolved'],state)
                if unknown and not attempt:
                    messages=messages+[dict(role='user',content='업무 조건에 내부 필드 이름이 포함됐습니다. 인증값은 서버가 준비하므로 제거하고, 실제 미정인 업무 조건만 자연스러운 확인 질문으로 unresolved에 적어 다시 추출하세요. 사용자가 요구하지 않은 형식 선호를 필수로 만들지 마세요.')]
                    continue
                if unknown:raise ValueError('고객에게 표시할 구체적인 질문을 구성하지 못했습니다.')
                from .requirement_contracts import RequirementQuestion
                for question in questions:RequirementQuestion.model_validate(question)
                pending=clarification_reply(state,questions,calls,unknown=unknown)
                if pending:
                    plan=_customer_question_plan(plan,questions)
                    return {**pending,'requirement_route':'await_input','requirement_ledger':plan,
                        'requirement_fingerprint':fingerprint,'requirement_digest':plan_digest(plan)}
                # Only app-managed credentials and unsolicited optional preferences were removed.
                plan['unresolved']=[]
                break
        except Exception as error:
            if error_observer:error_observer(error)
            # Provider exceptions may contain headers, prompts or customer data.
            detail='입력하신 조건은 보존했습니다. 업무 흐름을 구성하는 중 문제가 생겼습니다. 조건을 더 입력하지 않아도 같은 내용으로 다시 시도할 수 있습니다.'
            return dict(requirement_route='stop',requirement_required=True,requirement_ledger=None,
                        requirement_fingerprint=None,requirement_digest=None,
                        status='validation_failed',confirmed=False,saved_agent_id=None,issues=[detail],
                        pending_questions=[], draft=None,requirement_failure={'kind':'processing_error','retryable':True},
                        messages=state.get('messages',[])+[dict(role='assistant',content=detail)],
                        trace=state.get('trace',[])+[dict(node='requirements',label='요청 업무 목록',status='failed',detail=detail,
                                                         failure_kind=failure_kind,error_type=type(error).__name__,model_calls=calls)])
        return _completed(state, target, plan, fingerprint, calls)
    return check
