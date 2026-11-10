"""Extract a separate request ledger before generation; preserve it through repair."""
import hashlib
import json
import os
import ast
import re
from decimal import Decimal
from copy import deepcopy
from contracts.numeric_criteria import number_value

from .requirement_contracts import RequirementPlan


SYSTEM = '''사용자의 최종 업무 요구사항을 추출해 JSON으로 반환한다. 각 task는 실제 요청된 업무 한 가지다.
description은 짧은 업무명이다. 같은 업무를 반복하지 않는다. 출력 규격에 대한 설명이나 생각을 업무로 기록하지 않는다.
"원래 요청대로 생성", "다시 검토", "실행할 때 파일 제공", 자료 ID에 대한 질문은 설계 대화의 지시다. 이를 별도 실행 업무로 만들지 않고 원래 업무와 변경 조건을 반영한다.
메시지마다 task를 만들지 않는다. 예를 들어 "파일 ID가 무엇인가요?"나 "그대로 만들어 주세요"는 실행 업무 0개다. other에도 넣지 않는다. 반면 "개인정보를 삭제"처럼 실행할 업무지만 지원 종류가 없는 요청은 other로 보존한다.
source_message는 user_messages의 0-based index다. 원문 인용·ID·선행 연결은 앱이 부여한다.
사용자 원문과 후속 변경만 근거로 처리 순서대로 tasks를 작성한다. 자료 이름·열은 참고 데이터다.
compute=새 열의 계산식과 이름. filter=행 선별 비교조건. sort=정렬 열·방향·자료형. 정렬 요청만으로 filter를 만들지 않는다.
parse_dates=기존 날짜 문자열 열의 변환. fields=대상 열 목록, input_format=strptime 형식, timezone=명시된 IANA 시간대.
날짜 변환을 read 또는 compute로 대체하거나 parse_date 같은 함수 호출 수식으로 쓰지 않는다. 값이 미정이면 unresolved에 기록한다.
상수 비교는 value에 기준값, compare_field=null. 열 비교는 value=null, compare_field에 상대 열.
aggregate=그룹 열·count/sum·합계 원본 열. 집계 출력 열은 count/sum.
집계 결과에 요청한 이름이 따로 있으면 sum/count를 그 이름으로 바꾸는 rename(source,target)을 별도 기록한다.
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


def ledger_fingerprint(state):
    from agent_builder.capabilities.capability_gate import input_fingerprint
    # Version seals the interpretation contract as well as the input/catalog.
    return hashlib.sha256(('a4b.requirements.v4.document-table-schedule:' + input_fingerprint(state)).encode()).hexdigest()


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def grounded_schedule(state):
    from contracts.models import ScheduleSpec
    from agent_builder.conditions.condition_readiness import explicit_policies, source_catalog
    from workflow_engine.scheduling.recurrence import schedule_errors
    written = explicit_policies(state, source_catalog(state)[0])
    slots = {key.split('.', 1)[1]:item['value'] for key,item in written.items()
             if key.startswith('schedule.') and item.get('status') == 'specified'}
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
    json.dumps(plan,allow_nan=False)
    users=[m['content'] for m in state.get('messages',[]) if m['role']=='user']
    assets={a['id'] for a in state.get('resources',[])}
    ids=[t['id'] for t in plan['tasks']]
    if len(set(ids))!=len(ids):raise ValueError('요구사항 ID가 중복됩니다.')
    all_ids=set(ids)
    text='\n'.join(users)
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
        if spec['kind']=='schedule' and spec['schedule']!=grounded_schedule(state):
            raise ValueError('예약 요구사항이 사용자가 명시한 주기·시각·일자와 다릅니다.')
        if spec['kind'] in ('gmail_summary', 'gmail_scheduled_summary'):
            from .gmail_summary import gmail_summary_request
            gmail=gmail_summary_request(state)
            if (not gmail or spec['max_results']!=gmail['count']
                    or task['quote']!=gmail['request'] or spec['request']!=gmail['request']
                    or spec.get('summary_instructions')!=gmail.get('summary_instructions')):
                raise ValueError('Gmail 조회 건수·요약 요구를 사용자 원문에서 확인할 수 없습니다.')
            if spec['kind']=='gmail_scheduled_summary':
                if spec['schedule']!=gmail.get('schedule') or spec['query']!=gmail.get('query'):
                    raise ValueError('Gmail 예약 시각·조회 조건이 사용자 요청과 다릅니다.')
            elif gmail.get('schedule'):
                raise ValueError('Gmail 예약 요구사항의 실행 시각과 조회 기간이 빠졌습니다.')
        if spec['kind'] in ('extract_fields','receipt_items') and (len(set(spec['fields']))!=len(spec['fields']) or
                any(not field.strip() or field not in task['quote'] for field in spec['fields'])):
            raise ValueError('추출할 항목을 사용자 원문에서 확인할 수 없습니다.')
        if spec['kind']=='compute':
            from workflow_engine.table_formula import formula_columns
            formula_columns(spec['formula'])
            constants=[n.value for n in ast.walk(ast.parse(spec['formula'],mode='eval')) if isinstance(n,ast.Constant)]
            if any(abs(Decimal(str(v))) not in {abs(n) for n in numbers} for v in constants):
                raise ValueError('계산식의 숫자가 사용자 원문에서 확인되지 않습니다.')
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


def extraction_schema(user_count=None,asset_ids=None):
    schema=RequirementPlan.model_json_schema()
    # The extractor chooses the action and source turn. The application copies
    # that complete request rather than asking a second model to paraphrase it.
    analysis=schema['$defs']['DocumentAnalysis']
    analysis['properties'].pop('request');analysis['required'].remove('request')
    task=schema['$defs']['RequiredTask']
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
    if not isinstance(raw,dict) or set(raw)!={'tasks','unresolved'} or not isinstance(raw['tasks'],list):
        raise ValueError('업무 목록 응답 형식을 확인하세요.')
    tasks=[];previous=None
    for index,item in enumerate(raw['tasks'],1):
        if not isinstance(item,dict) or any(k in item for k in ('id','depends_on','quote')):
            raise ValueError('업무 ID·선행 연결·원문 인용은 앱 코드가 부여합니다.')
        users=[m['content'] for m in state['messages'] if m['role']=='user']
        source=item.get('source_message')
        if type(source) is not int or not 0<=source<len(users):
            raise ValueError('요구사항의 사용자 발화를 확인하지 못했습니다.')
        kind=(item.get('spec') or {}).get('kind')
        if kind=='document_analysis':
            if 'request' in item['spec']:raise ValueError('문서 분석 원문은 앱이 부여합니다.')
            item={**item,'spec':{**item['spec'],'request':users[source]}}
        dependency=[previous] if previous and kind not in ('read','policy') else []
        task={**item,'id':'r'+str(index),'depends_on':dependency,'quote':users[source]}
        tasks.append(task)
        if kind not in ('read','policy','export'):previous=task['id']
    return validate_plan(dict(tasks=tasks,unresolved=raw['unresolved']),state)


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
            return dict(requirement_route=target,requirement_required=True,requirement_ledger=plan,
                        requirement_fingerprint=ledger_fingerprint(state),requirement_digest=plan_digest(plan),
                        confirmed=False,saved_agent_id=None,
                        trace=state.get('trace',[])+[dict(node='requirements',label='요청 업무 목록',status='completed',
                            detail='원문의 Gmail 조회 건수와 본문 요약을 등록 계약으로 기록했습니다.',model_calls=0)])
        if setting=='off' and not state.get('requirement_required'):
            return dict(requirement_route=target,requirement_required=False)
        fingerprint=ledger_fingerprint(state)
        old=state.get('requirement_ledger')
        if (old and state.get('requirement_fingerprint')==fingerprint
                and state.get('requirement_digest')==plan_digest(old)):
            return dict(requirement_route=target,requirement_required=True)
        calls=0
        try:
            active=transport
            if active is None:
                active=requirement_transport(model,api_key)
            active.schema=extraction_schema(len([m for m in state['messages'] if m['role']=='user']),
                                            [a['id'] for a in state.get('resources',[])])
            payload={'user_messages':[m['content'] for m in state['messages'] if m['role']=='user'],
                     'resources':[{k:a[k] for k in ('id','name','kind','columns') if k in a}
                                  for a in state.get('resources',[])]}
            messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(payload,ensure_ascii=False))]
            for attempt in range(2):
                calls+=1
                response=active.invoke(messages)
                if isinstance(response,dict):
                    content=response['content']
                    if response.get('metrics',{}).get('done_reason') not in (None,'stop'):
                        raise ValueError('요구사항 추출 응답이 중간에 끝났습니다.')
                else:content=response.content
                try:plan=extract_plan(json.loads(content),state)
                except (ValueError,TypeError,KeyError) as error:
                    if attempt:raise
                    messages=messages+[dict(role='user',content='앞선 추출은 무효입니다. 다음 응답 계약 오류를 해결하여 원래 사용자 요구만 새로 추출하세요. '
                        '조건 값을 추측하거나 사용자 업무를 삭제하지 마세요. 오류: '+str(error)[:400])]
                    continue
                if plan['unresolved']:raise ValueError(' / '.join(plan['unresolved']))
                break
        except (ValueError,TypeError,KeyError,RuntimeError,OSError) as error:
            if error_observer:error_observer(error)
            detail='요청 업무 목록을 확정하지 못했습니다. '+str(error)[:400]
            return dict(requirement_route='stop',requirement_required=True,requirement_ledger=None,
                        requirement_fingerprint=None,requirement_digest=None,
                        status='validation_failed',confirmed=False,saved_agent_id=None,issues=[detail],
                        messages=state.get('messages',[])+[dict(role='assistant',content=detail)],
                        trace=state.get('trace',[])+[dict(node='requirements',label='요청 업무 목록',status='needs_input',detail=detail,model_calls=calls)])
        return dict(requirement_route=target,requirement_required=True,requirement_ledger=plan,
                    requirement_fingerprint=fingerprint,requirement_digest=plan_digest(plan),
                    confirmed=False,saved_agent_id=None,
                    trace=state.get('trace',[])+[dict(node='requirements',label='요청 업무 목록',status='completed',
                          detail=f"원문에서 {len(plan['tasks'])}개 요구사항을 생성 전에 별도로 기록했습니다.",model_calls=calls)])
    return check
