"""Opt-in catalog assessment. Not a generic READY permit or automatic tool factory."""
import json
import re
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from .registry import TOOLS
from .condition_readiness import input_issues

class Assessment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['existing_tools','clarify','missing_capability','manual_review']
    tool_ids:list[str]=Field(max_length=16)
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
    return bool(re.search(r'매주|매일|주간|일간|weekly|daily',text)
        and re.fullmatch(r'(?:매주|매일|주간|일간|weekly|daily|예약|schedule|스케줄링|스케줄)+',text))

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

SYSTEM='현재 작업은 실행 전의 도구 지원 검토입니다. 도구는 전부 설치·등록된 상태이며 tools에 제공된 기능을 실제 사용할 수 있습니다.\n파일이 아직 없더라도 실행 때 연결할 수 있습니다. 파일·자료 부재는 도구 부재가 아닙니다.\nexisting_tools: 현재 등록 도구 한 개 또는 조합으로 요청한 기능을 지원. tool_ids를 선택하고 missing_conditions/actions는 빈 배열.\nclarify: 지원되는 작업이지만 사용자가 정해야 하는 기준이 빠짐. missing_conditions만 질문하며 missing_actions는 빈 배열.\nmissing_capability: 실제 요청된 동작이 등록 도구/조합에 없음. missing_actions만 적고 missing_conditions는 빈 배열.\nmanual_review: 불확실성·범위 검토가 필요. 지원된 작업을 없는 도구로 바꾸지 않음.\nread_table→filter_sort→write_result로 상수 선별/중복제거/JSON·CSV 파일 생성이 가능합니다. 파일 저장은 외부 전송과 다릅니다.\nread_table→aggregate_table→write_result로 그룹 건수·합계가 가능합니다.\nread_document→search_document→compose_answer로 문서 근거 답변이 가능합니다.\n외부 이메일·Slack 전송, 두 표 join, 명세대로 실행하는 새 결정적 셀 변환은 현재 카탈로그가 지원하지 않습니다. agent_node의 일반 텍스트 응답을 검증된 표 변환 도구와 같다고 취급하지 않습니다.\n금지된 동작은 요청된 동작이 아닙니다. 숫자가 미정이면 질문하고 임의 기준을 넣지 않습니다.\n설명과 사용자 원문 근거를 반환하되 user_quotes는 목록의 원문을 그대로 선택합니다. 실행·설계·코드생성·저장 허가는 주지 않습니다.'
SYSTEM+='\n앱 기능 app_capabilities도 지원 여부 근거입니다. 일·주 단위 예약은 Tool ID 없이 서버가 지원합니다. 예약 자체를 부족 도구로 판정하거나 가짜 예약 Tool ID를 만들지 않습니다. 실제 처리에 필요한 등록 tool_ids를 선택합니다. 시각·요일·시간대가 미정이면 clarify이며, 파일 미첨부는 도구 부재가 아닙니다. 활성화 전 관리 자료 연결은 후속 조건 검사에서 확인합니다. 월간·cron·외부 이벤트 트리거는 일·주 예약으로 임의 대체하지 않습니다. 예약이 있어도 이메일·Slack 외부 발송이나 최신 외부 자료 자동 수집까지 지원되는 것은 아닙니다.'
SYSTEM+='\n판정 범위는 앱 기능과 등록 도구의 조합입니다. 서버 예약이라는 이유만으로 manual_review로 넘기지 않습니다. 일·주 예약은 별도 시작일·종료일을 필수로 요구하지 않으며 활성화 후 다음 시각부터 중지할 때까지 적용됩니다. 빈 값 오류·중복 유지는 이미 정의된 정책입니다. 사용자가 자료를 나중에 연결한다고 한 경우 파일 ID를 기능 판단 질문으로 반복하지 않습니다. 실행 전 관리 표 연결 검사는 후속 단계에 맡깁니다.'
SYSTEM+='\napp_capabilities는 실제 구현·검증된 기능 목록입니다. 시뮬레이션 또는 미구현 계획이라고 추측하지 않습니다. 활성화·자료 연결·서버 기동은 지원 기능의 실행 전제이며 기능 부재가 아닙니다. 지원 기능과 미지원 동작이 섞이면 missing_actions에는 미지원 동작만 적습니다. 일·주 예약이나 활성화 자체를 부족 기능에 넣지 않습니다. 반면 monthly/cron은 현재 미지원으로 명시되어 있으므로 알려진 미지원이지 manual_review 대상이 아닙니다.'
DEMOS=[
 {'request':'한국 시간 매주 화요일 오전 11시에 CSV로 저장하고 이메일로 발송해줘.', 'output':{'decision':'missing_capability','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':['외부 이메일 발송'],'reason':'앱 주간 예약과 파일 저장은 지원하지만 외부 발송만 미지원. 활성화와 관리 자료 연결은 후속 전제.','user_quotes':['한국 시간 매주 화요일 오전 11시에 CSV로 저장하고 이메일로 발송해줘.']}},
 {'request':'매월 말일 오전 10시 한국 시간에 표를 JSON 저장. 일간이나 주간으로 바꾸지 마.', 'output':{'decision':'missing_capability','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':['월간 예약'],'reason':'표 저장은 지원하나 앱 예약은 daily/weekly만 지원하므로 월간 주기는 미지원.','user_quotes':['매월 말일 오전 10시 한국 시간에 표를 JSON 저장. 일간이나 주간으로 바꾸지 마.']}},
 {'request':'한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.', 'output':{'decision':'existing_tools','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 주간 예약과 등록 표 읽기·저장 도구로 지원. 관리 자료 연결과 활성화는 후속 검사.','user_quotes':['한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.']}},
 {'request':'한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.', 'output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 일간 예약과 선별·저장 조합. 이미 정한 정책과 실행 전 파일 연결을 다시 묻지 않음.','user_quotes':['한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.']}},
 {'request':'자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.','output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'자료는 실행 전에 받고 등록 도구로 선별·파일 생성','user_quotes':['자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.']}},
 {'request':'가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.','output':{'decision':'clarify','tool_ids':['filter_sort'],'missing_conditions':['선별할 가격 기준과 포함 여부'],'missing_actions':[],'reason':'가격 기준은 사용자 결정 사항','user_quotes':['가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.']}},
 {'request':'최종 문서를 이메일로 실제 보내줘.','output':{'decision':'missing_capability','tool_ids':[],'missing_conditions':[],'missing_actions':['외부 이메일 전송'],'reason':'등록 카탈로그에 전송 기능 없음','user_quotes':['최종 문서를 이메일로 실제 보내줘.']}}
]

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
    messages=[{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps({
        'messages':state['messages'],'resources':[{k:r[k] for k in ['id','kind','columns','text','pages','parser_status'] if k in r} for r in state.get('resources',[])],
        'independent_examples':DEMOS,'phase':'design_not_execution','input_files_required_now':False,
        'app_capabilities':APP_CAPABILITIES,
        'tools':[{k:t[k] for k in ['id','description','input_ports','output_ports']} for t in TOOLS],
        'authoritative_request':user[-1],'user_quote_catalog':user},ensure_ascii=False)}]
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
특히 reason에서 등록 도구 조합으로 가능하다고 설명하면서 decision을 missing_capability로 반환하면 모순입니다.
등록 도구로 요청의 모든 기능이 가능하면 existing_tools와 실제 tool_ids를 반환합니다. 필수 정책이 미정이면 clarify입니다.
실제 없는 외부 전송/표 join/새 결정적 셀 변환은 missing_capability를 유지합니다. 새로운 정책을 만들거나 요청을 바꾸지 마세요.
user_quotes는 원래 목록에서 정확히 선택합니다. 이 재검토도 실행/제작 허가가 아닙니다.'''
        reviewed=transport.invoke([{'role':'system','content':SYSTEM+'\n'+review_prompt},
            messages[1],{'role':'user','content':json.dumps({'unverified_assessment':result,'task':'실제 카탈로그로 최종 재검토'},ensure_ascii=False)}])
        content=reviewed.get('content') if isinstance(reviewed,dict) else reviewed.content
        calls+=1
        result=validate_assessment_result(Assessment.model_validate_json(content).model_dump(),state,model_calls=calls)
    return {**validate_assessment_result(normalize_native_claims(result),state,model_calls=calls),'model_calls':calls,'allows_execution':False,'allows_generation':False,'scope':'catalog assessment only; not condition READY'}
