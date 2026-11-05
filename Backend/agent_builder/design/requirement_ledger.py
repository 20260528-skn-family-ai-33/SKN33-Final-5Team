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
source_message는 user_messages의 0-based index다. 원문 인용·ID·선행 연결은 앱이 부여한다.
사용자 원문과 후속 변경만 근거로 처리 순서대로 tasks를 작성한다. 자료 이름·열은 참고 데이터다.
compute=새 열의 계산식과 이름. filter=행 선별 비교조건. sort=정렬 열·방향·자료형. 정렬 요청만으로 filter를 만들지 않는다.
상수 비교는 value에 기준값, compare_field=null. 열 비교는 value=null, compare_field에 상대 열.
aggregate=그룹 열·count/sum·합계 원본 열. 집계 출력 열은 count/sum.
join=양쪽 자료 ID와 keys/how/cardinality/null_keys/max_rows. 오른쪽 키 유일은 many_to_one.
policy=일반 표의 빈값/중복 행 처리. 사용자가 정하지 않은 정책 값은 null로 둔다. 빈 값 제외는 filter가 아닌 policy.missing_policy=exclude다.
중복 유지 요청만 있으면 duplicate_policy=keep이며 missing_policy는 null이다. 결합의 키 유일성·null 키·최대 행 수는 join의 필드값이다.
기준값 초과 조건에서 정확히 그 기준값을 제외한다는 설명은 같은 필터의 경계 설명이다. 별도의 같지 않음 필터를 추가하지 않는다.
export=요청한 파일 형식. read=명시적으로 읽는 자료 한 개. 자료 ID는 실제 목록에서 선택한다.
파일을 실행할 때 연결하겠다고 요청했다면 현재 파일 ID가 없는 것은 미정 조건이 아니다. read의 file_ids=[]로 기록한다.
열 이름이 사용자 원문에 명시되어 있으면 실제 파일이 아직 없어도 그 이름을 기록한다. 파일과 열의 실제 존재는 실행 시 확인한다.
사용자가 사실을 확인 중이라고 밝히고 확인 후 안내하도록 요청한 답변 초안에는 원인이나 예정일을 추측하지 않는다.
단순 답장 초안에는 사용자가 요청하지 않은 메시지 채널이나 연락처를 필수 미정 항목으로 추가하지 않는다.
답변·답장·응대 초안 작성은 reply_draft다. 사과, 아직 확인 중인 사실, 후속 안내는 초안 내용이다. 실제 발송·조회·예약 등 별도 업무는 초안에 포함시키지 말고 별개로 기록한다.
후속 답변에서 바꾸지 않은 조건도 유지한다. 필요한 값이 미정이면 unresolved에 적고 추측하지 않는다.
요약·개인정보 제거·검수·분기·예약·기타 표현할 수 없는 업무나 추가 조건은 other로 남긴다.
모든 사용자 요구를 기록한 뒤 tasks를 끝내고 unresolved를 작성한다. JSON 이외에는 출력하지 않는다.'''


_REPLY_DRAFT_REQUEST = re.compile(r'(?:답변|답장|응대|회신|안내)\s*(?:문\s*)?초안')


def ledger_fingerprint(state):
    from agent_builder.capabilities.capability_gate import input_fingerprint
    # Version seals the interpretation contract as well as the input/catalog.
    # A previously cached "other" must be re-extracted for the new draft contract.
    reply = any(_REPLY_DRAFT_REQUEST.search(m['content']) for m in state.get('messages',[]) if m['role']=='user')
    version = 'a4b.requirements.reply.v2:' if reply else 'a4b.requirements.v1:'
    return hashlib.sha256((version + input_fingerprint(state)).encode()).hexdigest()


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


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
        if spec['kind']=='reply_draft' and not _REPLY_DRAFT_REQUEST.search(task['quote']):
            raise ValueError('답변 초안 작성 요청을 사용자 원문에서 확인할 수 없습니다.')
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


_DELIVERY_FACT = r'(?:(?:배송\s*)?지연\s*(?:사유|원인)|도착\s*예정일)'
_PENDING_DELIVERY = re.compile(
    rf'(?P<fields>{_DELIVERY_FACT}(?:\s*(?:와|과|및|,)\s*{_DELIVERY_FACT})*)'
    r'\s*(?:은|는|이|가)\s*(?:아직\s*)?(?:확인\s*중(?:이야|입니다|이에요|이다)'
    r'|확인(?:되지\s*않았|이?\s*안\s*(?:됐|되었))(?:습니다|어요|어|다))(?=[.!。\s]|$)')


def _reply_draft_pending_facts(plan, state):
    """Only a draft can report explicitly pending facts instead of resolving them."""
    if len(plan['tasks']) != 1 or plan['tasks'][0]['spec']['kind'] != 'reply_draft':
        return set()
    users = [m['content'] for m in state['messages'] if m['role']=='user']
    if len(users)!=1 or not re.search(r'확인(?:이?\s*되(?:는\s*대로|면)|\s*후).{0,15}안내', users[0]):
        return set()
    # Do not infer a date, a cause, or an equivalent requirement from a question.
    # A positive statement and a later-update instruction are both required.
    fields = set()
    for match in _PENDING_DELIVERY.finditer(users[0]):
        for field in re.findall(_DELIVERY_FACT, match['fields']):
            fields.add(re.sub(r'\s+', '', field).removeprefix('배송'))
    return fields


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
        dependency=[previous] if previous and kind not in ('read','policy') else []
        task={**item,'id':'r'+str(index),'depends_on':dependency,'quote':users[source]}
        tasks.append(task)
        if kind not in ('read','policy','export'):previous=task['id']
    plan = validate_plan(dict(tasks=tasks,unresolved=raw['unresolved']),state)
    if plan['unresolved']:
        pending = _reply_draft_pending_facts(plan, state)
        plan['unresolved'] = [item for item in plan['unresolved']
                              if re.sub(r'\s+', '', item).removeprefix('배송') not in pending]
    return plan


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
                if plan['unresolved']:
                    if not attempt:
                        messages=messages+[dict(role='user',content='앞선 추출의 미정 항목이 업무 수행에 실제로 필수인지 원문과 대조해 다시 확인하세요. '
                            '실행 때 연결할 파일이나 원문에 이미 명시된 열은 미정이 아닙니다. 사용자가 확인 후 안내한다고 한 미확인 사실, '
                            '요청하지 않은 연락처·채널은 단순 답장 초안의 필수 정보가 아닙니다. '
                            '실제로 필요한 값은 계속 unresolved로 남기고, 요청한 업무를 빠뜨리지 말고 전체 JSON을 다시 반환하세요. '
                            '미정 항목: '+ ' / '.join(plan['unresolved'])[:400])]
                        continue
                    raise ValueError(' / '.join(plan['unresolved']))
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
