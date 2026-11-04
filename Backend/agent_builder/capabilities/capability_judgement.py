"""Opt-in catalog assessment. Not a generic READY permit or automatic tool factory."""
import json
import os
import re
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from workflow_engine.catalog.registry import TOOLS
from workflow_engine.catalog.canonical import planner_catalog
from agent_builder.conditions.condition_readiness import input_issues

class Assessment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['existing_tools','clarify','missing_capability','manual_review']
    tool_ids:list[str]=Field(max_length=62)
    missing_conditions:list[str]=Field(max_length=5)
    missing_actions:list[str]=Field(max_length=3)
    reason:str=Field(min_length=1,max_length=1500)
    user_quotes:list[str]=Field(min_length=1,max_length=4)


class AssessmentConflictError(ValueError):
    """Keep contradictory reports as diagnostics, never as routing authority."""
    def __init__(self,result,model_calls):
        super().__init__('지원 판정과 누락 항목이 서로 모순됩니다.')
        self.reported={key:result[key] for key in ('decision','missing_conditions','missing_actions')}
        self.model_calls=model_calls


def validate_assessment_result(result,state,*,model_calls=0):
    # Check both model responses and persisted results reused after non-answers.
    # Runtime metadata is allowed on persisted results; core fields stay strict.
    value=Assessment.model_validate({key:result.get(key) for key in Assessment.model_fields},strict=True).model_dump()
    decision=value['decision'];conditions=value['missing_conditions'];actions=value['missing_actions']
    if ((decision=='existing_tools' and (not value['tool_ids'] or conditions or actions))
        or (decision=='clarify' and (not conditions or actions))
        or (decision=='missing_capability' and (not actions or conditions))):
        raise AssessmentConflictError(value,model_calls)
    if (not value['reason'].strip()
        or any(not text.strip() for key in ('missing_conditions','missing_actions') for text in value[key])):
        raise ValueError('지원 판단의 사유·누락 항목은 공백일 수 없습니다.')
    if len(set(value['tool_ids']))!=len(value['tool_ids']) or any(t not in {t['id'] for t in TOOLS} for t in value['tool_ids']):
        raise ValueError('등록되지 않았거나 중복된 도구 판단입니다.')
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if any(not q.strip() or not any(q in text for text in user) for q in value['user_quotes']):
        raise ValueError('요청 원문 근거를 확인하지 못했습니다.')
    return result

# Native app capabilities are not tool IDs and do not grant runtime permission.
APP_CAPABILITIES=[{
    'id':'workflow_schedule','implementation_status':'implemented and verified on this server; not a simulation',
    'supported_frequencies':['daily','weekly'],
    'required_fields':['frequency','hour','minute','timezone'],
    'weekly_required_fields':['weekday'],
    'source_requirement':'managed table before activation',
    'activation':'validated saved workflow plus separate explicit activation',
    'defaults':{'start':'next future occurrence after activation','end':'continues until stopped; no end date required'},
    'limitations':['server must be running','stored managed source reused; no automatic external refresh',
                   'no monthly, cron or arbitrary external trigger in AgentBuilder']
}]

def _native_schedule_label(action):
    """Recognize only standalone native recurrence labels, never arbitrary tasks.

    Unknown wording is retained for review. External delivery, monthly/cron,
    data refresh and other side effects cannot match this closed vocabulary.
    """
    text=re.sub(r'[\s()·:,.]', '', action).casefold()
    text=re.sub(r'[월화수목금토일]요일|\d+(?:시|분)', '', text)
    # Time-of-day words are part of the recurrence label ("매주 월요일 오전 8 시 예약", live 2026-11-03).
    return bool(re.search(r'매주|매일|주간|일간|weekly|daily',text)
        and re.fullmatch(r'(?:매주|매일|주간|일간|weekly|daily|예약|schedule|스케줄링|스케줄'
                         r'|오전|오후|아침|저녁|정오|자정|마다|에|한국시간|kst|시각)+',text))

_NATIVE_SCHEDULE_REQUEST=re.compile(r'매일|매주|[월화수목금토일]요일\s*마다|daily|weekly',re.I)
_NOT_NATIVE_SCHEDULE=re.compile(r'매월|매달|월간|매년|cron|크론',re.I)

def reconcile_native_schedule(result,state):
    """A refusal whose only gap was the app's own daily/weekly schedule goes to the condition checker.

    Live 2026-11-03 (B-8): "매주 월요일 오전 8시마다 … 저장해줘" was refused twice (initial and review)
    with only "주간 예약"/"매주 월요일 오전 8 시 예약" as the gap, although the app implements it.
    This is not READY: the result is marked so the grounded condition checker must validate the
    schedule slots (frequency, weekday, time, timezone, managed table) before any draft.
    """
    if (result['decision']!='manual_review' or not result.get('native_app_claims_removed')
            or result['missing_actions'] or result['missing_conditions'] or not result['tool_ids']):
        return result
    if any(t not in {tool['id'] for tool in TOOLS} for t in result['tool_ids']):return result
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if not user or not _NATIVE_SCHEDULE_REQUEST.search(user[-1]) or _NOT_NATIVE_SCHEDULE.search(user[-1]):return result
    return {**result,'decision':'existing_tools','native_schedule_reconciled':True,
        'reason':result['reason']+' 부족 항목이 앱의 일·주 예약뿐이라 예약 조건 검사로 넘깁니다.'}

def normalize_native_claims(result):
    """Code checks explicit app facts; never turn a model refusal into READY."""
    if result['decision']!='missing_capability':return result
    removed=[a for a in result['missing_actions'] if _native_schedule_label(a)]
    if not removed:return result
    remaining=[a for a in result['missing_actions'] if not _native_schedule_label(a)]
    return {**result,'decision':'missing_capability' if remaining else 'manual_review',
        'model_missing_actions':list(result['missing_actions']),
        'native_app_claims_removed':removed,'missing_actions':remaining,
        'reason':result['reason']+' 일·주 예약 자체는 앱의 지원 기능이므로 코드 대조 후 부족 목록에서 제외했습니다.'}

# Unsupported channels remain gaps; registered Gmail operations are checked separately.
# A channel followed closely by a send verb is a request for external delivery; a negation
# right after it ("보내지 마", "말고") is not.
# filter_sort already implements every missing/duplicate policy (registry: missing_policy
# exclude/keep/error, duplicate_policy first/last/keep/error). A refusal whose only items name
# such a policy, while the request states that policy in a form code reads, is corrected.
_POLICY_ITEM = re.compile(r'(빈|결측|누락|중복|같은|겹치)')
_NOT_POLICY = re.compile(r'메일|슬랙|slack|전송|발송|조인|join|합치|합쳐|병합|월간|cron|채우|채워|평균|대체|보간|외부|상충|모순|충돌|우선순위|vs|또는|둘 중', re.I)

def registered_policy_claims(result,state):
    """Only ever moves a policy-only refusal/clarification to existing_tools, when code reads the
    stated policy from the latest request; anything else is returned unchanged."""
    if result['decision'] not in ('missing_capability','clarify'):return result
    items=result['missing_actions'] if result['decision']=='missing_capability' else result['missing_conditions']
    if not items or any(not _POLICY_ITEM.search(i) or _NOT_POLICY.search(i) for i in items):return result
    from agent_builder.conditions.condition_readiness import policy_clauses
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    columns={c for r in state.get('resources',[]) if r.get('kind')=='table' for c in r.get('columns',[]) if isinstance(c,str)}
    stated={k for k,v,_ in policy_clauses(user[-1],columns) if v!='conflict'} if user else set()
    named={'missing.policy' if re.search(r'빈|결측|누락',i) else 'duplicate.policy' for i in items}
    if not stated or not named<=stated:return result
    tools=list(dict.fromkeys([*result['tool_ids'],'read_table','filter_sort']))
    return {**result,'decision':'existing_tools','tool_ids':tools,'missing_conditions':[],'missing_actions':[],
        'model_decision':result['decision'],'model_items':list(items),'code_registered_policy':sorted(named),
        'reason':result['reason']+' 요청한 빈 값·중복 정책은 filter_sort에 등록된 정책이라 코드 대조 후 지원으로 바로잡았습니다.'}

_EXTERNAL_DELIVERY=re.compile(
    r'(?<![가-힣A-Za-z0-9_])'
    r'(이메일|e-?mail|메일|슬랙|slack|문자\s*메시지|문자|sms|카카오톡|카톡|텔레그램|팩스|웹훅|webhook)'
    r'(?:(?:\s*(?:으?로|에(?:게|다)?|을|를)\s*)[^.!?\n]{0,24}?|\s+)'
    r'((?:보내|알려|올려)(?:줘|주세|라|요|야|줄|기|고|지|(?=\s|[.!?\n]|$))|'
    r'(?:발송|전송|공유|전달)(?:해|하))',re.I)


def _external_delivery(text):
    # Quoted column values are data, not requests to send them. Keep offsets so
    # separated text cannot become a new apparent channel/action pair.
    text=re.sub(r'(["\']).*?\1',lambda m:' '*len(m.group(0)),text)
    for match in _EXTERNAL_DELIVERY.finditer(text):
        after=text[match.end():]
        before=text[match.end(1):match.start(2)]
        if re.search(r'(?:안|못)\s*$',before):continue
        # A particle may sit between the verb stem and the negation: "보내지는 마", "보내지도 말고".
        # Live 2026-11-03 (B-6 n01): "메일로 보내지는 마" was recorded as an external send request.
        if re.match(r'\s*(?:지\s*)?(?:는|도)?\s*(?:마|말|않)|\s*지(?:는|도)\s*(?:마|말|않)',after):continue
        yield match

def external_delivery_claims(result,state):
    """Code checks the registered catalogue; only ever moves toward missing_capability."""
    if result['decision']=='missing_capability':return result
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    match=next(_external_delivery(user[-1]),None) if user else None
    if not match:return result
    if match.group(1).lower() in ('이메일','email','e-mail','메일') and any(t['id']=='email.send' for t in TOOLS):return result
    return {**result,'decision':'missing_capability','missing_conditions':[],
        'missing_actions':['외부 '+match.group(1)+' 전송'],
        'model_decision':result['decision'],'model_missing_conditions':list(result['missing_conditions']),
        'code_external_delivery':match.group(0),
        'reason':result['reason']+' 등록 도구에는 외부 전송 기능이 없어 코드 대조 후 외부 전송을 부족 기능으로 기록했습니다.'}

SYSTEM='현재 작업은 실행 전의 도구 지원 검토입니다. tools에 등록된 실행 동작·입출력·범위를 확인합니다. 계정 연결이 필요한 도구는 등록된 기능이지만 실행 전 연결이 필요합니다.\n파일이 아직 없더라도 실행 때 연결할 수 있습니다. 파일·자료 부재는 도구 부재가 아닙니다.\nexisting_tools: 현재 등록 도구 한 개 또는 조합으로 요청한 기능을 지원. tool_ids를 선택하고 missing_conditions/actions는 빈 배열.\nclarify: 지원되는 작업이지만 사용자가 정해야 하는 기준이 빠짐. missing_conditions만 질문하며 missing_actions는 빈 배열.\nmissing_capability: 실제 요청된 동작이 등록 도구/조합에 없음. missing_actions만 적고 missing_conditions는 빈 배열.\nmanual_review: 불확실성·범위 검토가 필요. 지원된 작업을 없는 도구로 바꾸지 않음.\nread_table→filter_sort→write_result로 상수 선별/중복제거/JSON·CSV 파일 생성이 가능합니다. 파일 저장은 외부 전송과 다릅니다.\nread_table→aggregate_table→write_result로 그룹 건수·합계가 가능합니다.\nread_document→search_document→compose_answer로 문서 근거 답변이 가능합니다.\ncalculate_columns는 명시된 열과 숫자의 사칙연산·나머지로 새 열을 만들거나 열 하나의 이름을 바꾸고, 입력 형식·시간대가 명시된 날짜 열을 변환합니다. text_transform은 앞 단계 텍스트에서 지정된 문자열을 그대로 바꿉니다. base64_codec은 텍스트 Base64 인코딩·디코딩, text_hash는 UTF-8 텍스트 SHA-256을 지원합니다. 추가 문자열·문서 편집은 해당 등록 도구의 operation_contracts 범위에서 지원합니다. 식이나 결과 열이 빠지면 확인합니다. 다른 임의 셀 변환과 수식 함수는 지원한다고 가정하지 않습니다. 두 표 join은 join_tables, 이메일은 email.* 등록 도구를 사용합니다. 메일·캘린더 실행에는 계정 연결이 필요합니다. Slack 전송은 지원하지 않습니다. agent_node의 일반 텍스트 응답을 검증된 표 변환 도구와 같다고 취급하지 않습니다.\n금지된 동작은 요청된 동작이 아닙니다. 숫자가 미정이면 질문하고 임의 기준을 넣지 않습니다.\n설명과 사용자 원문 근거를 반환하되 user_quotes는 목록의 원문을 그대로 선택합니다. 실행·설계·코드생성·저장 허가는 주지 않습니다.'
SYSTEM+='\n앱 기능 app_capabilities도 지원 여부 근거입니다. 일·주 단위 예약은 Tool ID 없이 서버가 지원합니다. 예약 자체를 부족 도구로 판정하거나 가짜 예약 Tool ID를 만들지 않습니다. 실제 처리에 필요한 등록 tool_ids를 선택합니다. 시각·요일·시간대가 미정이면 clarify이며, 파일 미첨부는 도구 부재가 아닙니다. 활성화 전 관리 자료 연결은 후속 조건 검사에서 확인합니다. 월간·cron·외부 이벤트 트리거는 일·주 예약으로 임의 대체하지 않습니다. 예약 자체가 계정 연결 권한을 제공하지는 않습니다. 이메일은 등록 Gmail 도구와 연결 계정을, 캘린더는 Google Calendar 계정을 사용합니다. Slack·자동 원격 자료 수집은 별도 등록 도구 없이는 지원하지 않습니다.'
SYSTEM+='\n판정 범위는 앱 기능과 등록 도구의 조합입니다. 서버 예약이라는 이유만으로 manual_review로 넘기지 않습니다. 일·주 예약은 별도 시작일·종료일을 필수로 요구하지 않으며 활성화 후 다음 시각부터 중지할 때까지 적용됩니다. 빈 값 제외·유지·오류와 중복 첫 행·마지막 행·유지·오류(중복이 있으면 실행 중단)는 filter_sort에 이미 정의된 정책입니다. 이 정책을 요청한 것은 기능 부족이 아닙니다. 사용자가 자료를 나중에 연결한다고 한 경우 파일 ID를 기능 판단 질문으로 반복하지 않습니다. 실행 전 관리 표 연결 검사는 후속 단계에 맡깁니다.'
SYSTEM+='\napp_capabilities는 실제 구현·검증된 기능 목록입니다. 시뮬레이션 또는 미구현 계획이라고 추측하지 않습니다. 활성화·자료 연결·서버 기동은 지원 기능의 실행 전제이며 기능 부재가 아닙니다. 지원 기능과 미지원 동작이 섞이면 missing_actions에는 미지원 동작만 적습니다. 일·주 예약이나 활성화 자체를 부족 기능에 넣지 않습니다. 반면 monthly/cron은 현재 미지원으로 명시되어 있으므로 알려진 미지원이지 manual_review 대상이 아닙니다.'
SYSTEM+='\n조건끼리 서로 모순되거나(예: 같은 대상에 두 가지 정책) 기준·값·단위가 미정인 것은 기능 부족이 아니라 사용자가 정할 조건이므로 clarify입니다. 모순 해결, 기준 선택, 값 명시, 정책 우선순위 결정을 missing_actions에 적지 않고 missing_conditions에 적습니다. 등록 도구가 지원하는 작업(상수 비교 선별, 빈 값 정책, 첫 행·마지막 행 중복 제거, 파일 저장)을 조건이 모순이라는 이유로 미지원이라 하지 않습니다.'
DEMOS=[
 {'request':'매월 말일 오전 10시 한국 시간에 표를 JSON 저장. 일간이나 주간으로 바꾸지 마.', 'output':{'decision':'missing_capability','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':['월간 예약'],'reason':'표 저장은 지원하나 앱 예약은 daily/weekly만 지원하므로 월간 주기는 미지원.','user_quotes':['매월 말일 오전 10시 한국 시간에 표를 JSON 저장. 일간이나 주간으로 바꾸지 마.']}},
 {'request':'한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.', 'output':{'decision':'existing_tools','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 주간 예약과 등록 표 읽기·저장 도구로 지원. 관리 자료 연결과 활성화는 후속 검사.','user_quotes':['한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.']}},
 {'request':'한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.', 'output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 일간 예약과 선별·저장 조합. 이미 정한 정책과 실행 전 파일 연결을 다시 묻지 않음.','user_quotes':['한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.']}},
 {'request':'자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.','output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'자료는 실행 전에 받고 등록 도구로 선별·파일 생성','user_quotes':['자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.']}},
 {'request':'가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.','output':{'decision':'clarify','tool_ids':['filter_sort'],'missing_conditions':['선별할 가격 기준과 포함 여부'],'missing_actions':[],'reason':'가격 기준은 사용자 결정 사항','user_quotes':['가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.']}},
]

_NEW_TASK_WORDS=re.compile(r'줘|주세요|만들|정리|저장|보내|골라|남겨|뽑|계산|비교|합계|집계|요약|예약|매일|매주|변환|추출',re.I)

def _task_window(state):
    """Messages the capability judgment sees: the whole conversation, or only a new task request.

    Live 2026-11-03 (A5): after a receipt request, a later sales-comparison request was refused with
    "영수증 OCR 부족" on every turn because the whole conversation was judged as one request.
    A latest user message that names work (a task word, longer than a short reply) and does not
    answer a pending question starts a new task, so only it is judged. Answers to pending
    questions keep the whole conversation, which they need ("100만원 초과로 해줘", "상품명, 수량").
    """
    if os.getenv('A4B_CAPABILITY_CONTEXT','window')=='full':return state['messages']
    messages=state.get('messages',[])
    if not messages or messages[-1].get('role')!='user' or state.get('pending_questions') or state.get('capability_questions'):
        return messages
    text=re.sub(r'\s+','',messages[-1]['content'])
    if len(text)<=12 or not _NEW_TASK_WORDS.search(text) or len([m for m in messages if m.get('role')=='user'])<2:
        return messages
    return [messages[-1]]

def assess_capability(state,transport):
    faults=input_issues(state)
    if faults:return {'decision':'system_error','issues':faults,'model_calls':0,'allows_execution':False,'allows_generation':False}
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if not user:raise ValueError('사용자 요청이 필요합니다.')
    schema=Assessment.model_json_schema()
    schema['properties']['tool_ids']['items']['enum']=[t['id'] for t in TOOLS]
    schema['properties']['user_quotes']['items']['enum']=user
    variants=[]
    from copy import deepcopy
    for decision in ['existing_tools','clarify','missing_capability','manual_review']:
        branch=deepcopy(schema);props=branch['properties'];props['decision']={'type':'string','enum':[decision]}
        if decision=='existing_tools':
            props['tool_ids']['minItems']=1;props['missing_conditions']['maxItems']=0;props['missing_actions']['maxItems']=0
        elif decision=='clarify':
            props['missing_conditions']['minItems']=1;props['missing_actions']['maxItems']=0
        elif decision=='missing_capability':
            props['missing_actions']['minItems']=1;props['missing_conditions']['maxItems']=0
        variants.append(branch)
    transport.schema={'anyOf':variants}
    window=_task_window(state)
    messages=[{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps({
        'messages':window,'resources':[{k:r[k] for k in ['id','kind','columns','text','pages','parser_status'] if k in r} for r in state.get('resources',[])],
        'independent_examples':DEMOS,'phase':'design_not_execution','input_files_required_now':False,
        'app_capabilities':APP_CAPABILITIES,
        'tools':planner_catalog(TOOLS),
        'authoritative_request':user[-1],'user_quote_catalog':[m['content'] for m in window if m.get('role')=='user']},ensure_ascii=False)}]
    reply=transport.invoke(messages)
    content=reply.get('content') if isinstance(reply,dict) else reply.content
    result=validate_assessment_result(Assessment.model_validate_json(content).model_dump(),state,model_calls=1)
    calls=1
    if result['decision']=='missing_capability':
        review_prompt='''이전 지원 판단은 미검증 후보입니다. 요청과 실제 tools 및 app_capabilities를 모두 다시 대조하여 최종 Assessment JSON 하나를 반환합니다.
가장 먼저 missing_actions 각 항목을 검토합니다. 앱의 daily/weekly 예약은 이미 구현된 지원 기능이므로 '주간 예약', '매주 ... 시각', '예약 활성화'를 부족 기능으로 적었다면 해당 항목을 제거합니다.
이 시점은 새 워크플로우 설계 전입니다. 특정 사용자의 활성화 설정이 아직 없다는 것은 당연하며 기능 부재가 아닙니다. 기존 활성화된 예약이 있어야 지원한다고 판단하지 않습니다.
요청에 미지원 외부 전송이 함께 있으면 외부 전송만 missing_actions에 남깁니다. 월간/cron은 실제 미지원으로 유지합니다.
누락된 입력 파일이나 실행 전 자료 연결은 기능 부족이 아닙니다. 도구 설명에 이미 있는 기능을 없다고 하지 마세요.
조건 사이의 모순, 미정 기준·값·단위, 정책 선택은 기능이 아니라 사용자 결정입니다. missing_actions에 이런 항목만 남는다면 clarify로 바꾸고 그 항목을 missing_conditions로 옮깁니다.
특히 reason에서 등록 도구 조합으로 가능하다고 설명하면서 decision을 missing_capability로 반환하면 모순입니다.
등록 도구로 요청의 모든 기능이 가능하면 existing_tools와 실제 tool_ids를 반환합니다. 필수 정책이 미정이면 clarify입니다.
표 join은 join_tables의 실제 등록 operation·입출력·정책과 대조합니다. 이메일·캘린더처럼 등록된 외부 동작은 기능 지원과 계정 연결 전제를 구분하고, Slack처럼 등록되지 않은 전송은 미지원으로 유지합니다. calculate_columns의 명시적 사칙연산·열 이름 변경과 text_transform의 문자열 바꾸기는 등록된 지원 기능입니다. 그 밖의 임의 셀 변환은 지원한다고 가정하지 마세요. 새로운 정책을 만들거나 요청을 바꾸지 마세요.
user_quotes는 원래 목록에서 정확히 선택합니다. 이 재검토도 실행/제작 허가가 아닙니다.'''
        reviewed=transport.invoke([{'role':'system','content':SYSTEM+'\n'+review_prompt},
            messages[1],{'role':'user','content':json.dumps({'unverified_assessment':result,'task':'실제 카탈로그로 최종 재검토'},ensure_ascii=False)}])
        content=reviewed.get('content') if isinstance(reviewed,dict) else reviewed.content
        calls+=1
        result=validate_assessment_result(Assessment.model_validate_json(content).model_dump(),state,model_calls=calls)
    return {**validate_assessment_result(external_delivery_claims(registered_policy_claims(reconcile_native_schedule(normalize_native_claims(result),state),state),state),state,model_calls=calls),'model_calls':calls,'allows_execution':False,'allows_generation':False,'scope':'catalog assessment only; not condition READY'}
