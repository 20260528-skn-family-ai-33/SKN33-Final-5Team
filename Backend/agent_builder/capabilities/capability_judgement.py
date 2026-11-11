"""Opt-in catalog assessment. Not a generic READY permit or automatic tool factory."""
import json
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
    user=[m['content'] for m in _assessment_messages(state) if m.get('role')=='user']
    if any(not q.strip() or not any(q in text for text in user) for q in value['user_quotes']):
        raise ValueError('요청 원문 근거를 확인하지 못했습니다.')
    return result

# Native app capabilities are not tool IDs and do not grant runtime permission.
APP_CAPABILITIES=[{
    'id':'workflow_schedule','implementation_status':'implemented and verified on this server; not a simulation',
    'supported_frequencies':['hourly','daily','weekly','monthly'],
    'required_fields':['frequency','minute','timezone'],
    'hour_required_frequencies':['daily','weekly','monthly'],
    'weekly_required_fields':['weekday'],
    'monthly_required_fields':'monthday (1..31) or month_end=true; missing dates are skipped',
    'source_requirement':'all actual required inputs ready before activation; a file is required only for a file-reading step',
    'activation':'validated saved workflow plus separate explicit activation',
    'defaults':{'start':'next future occurrence after activation','end':'continues until stopped; no end date required'},
    'limitations':['server must be running','external requests require registered operation contracts and connected credentials',
                   'no yearly, cron or arbitrary external trigger in AgentBuilder']
}]

def _native_schedule_label(action):
    """Recognize only standalone native recurrence labels, never arbitrary tasks.

    Unknown wording is retained for review. External delivery, yearly/cron,
    data refresh and other side effects cannot match this closed vocabulary.
    """
    text=re.sub(r'[\s()·:,.]', '', action).casefold()
    text=text.replace('한시간마다', 'hourly').replace('1시간마다', 'hourly')
    text=re.sub(r'[월화수목금토일]요일|\d+(?:시|분|일)', '', text)
    # Time-of-day words are part of the recurrence label ("매주 월요일 오전 8 시 예약", live 2026-11-03).
    return bool(re.search(r'매시간|매시|시간마다|매월|매달|월간|monthly|hourly|매주|매일|주간|일간|weekly|daily',text)
        and re.fullmatch(r'(?:매시간|매시|시간마다|매월|매달|월간|monthly|hourly|말일|마지막날|정각|매주|매일|주간|일간|weekly|daily|예약|schedule|스케줄링|스케줄'
                         r'|오전|오후|아침|저녁|정오|자정|마다|에|한국시간|kst|시각)+',text))

_NATIVE_SCHEDULE_REQUEST=re.compile(r'매시간|매시|시간마다|매월|매달|월간|monthly|hourly|매일|매주|[월화수목금토일]요일\s*마다|daily|weekly',re.I)
_NOT_NATIVE_SCHEDULE=re.compile(r'매년|yearly|cron|크론',re.I)

def reconcile_native_schedule(result,state):
    """A refusal whose only gap was the app's own hourly/daily/weekly/monthly schedule goes to the condition checker.

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
        'reason':result['reason']+' 부족 항목이 앱의 시·일·주·월 예약뿐이라 예약 조건 검사로 넘깁니다.'}

def normalize_native_claims(result):
    """Code checks explicit app facts; never turn a model refusal into READY."""
    if result['decision']!='missing_capability':return result
    removed=[a for a in result['missing_actions'] if _native_schedule_label(a)]
    if not removed:return result
    remaining=[a for a in result['missing_actions'] if not _native_schedule_label(a)]
    return {**result,'decision':'missing_capability' if remaining else 'manual_review',
        'model_missing_actions':list(result['missing_actions']),
        'native_app_claims_removed':removed,'missing_actions':remaining,
        'reason':result['reason']+' 시·일·주·월 예약 자체는 앱의 지원 기능이므로 코드 대조 후 부족 목록에서 제외했습니다.'}

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
SYSTEM+='\n앱 기능 app_capabilities도 지원 여부 근거입니다. 시·일·주·월 단위 예약은 Tool ID 없이 서버가 지원합니다. 예약 자체를 부족 도구로 판정하거나 가짜 예약 Tool ID를 만들지 않습니다. 실제 처리에 필요한 등록 tool_ids를 선택합니다. 시각·요일·시간대가 미정이면 clarify이며, 파일 미첨부는 도구 부재가 아닙니다. 활성화 전 실제 필수 입력 준비는 후속 검사에서 확인하며 파일이 필요한 작업에만 자료를 연결합니다. 연간·cron·외부 이벤트 트리거는 시·일·주·월 예약으로 임의 대체하지 않습니다. 예약 자체가 계정 연결 권한을 제공하지는 않습니다. 이메일은 등록 Gmail 도구와 연결 계정을, 캘린더는 Google Calendar 계정을 사용합니다. Slack·자동 원격 자료 수집은 별도 등록 도구 없이는 지원하지 않습니다.'
SYSTEM+='\n판정 범위는 앱 기능과 등록 도구의 조합입니다. 서버 예약이라는 이유만으로 manual_review로 넘기지 않습니다. 시·일·주·월 예약은 별도 시작일·종료일을 필수로 요구하지 않으며 활성화 후 다음 시각부터 중지할 때까지 적용됩니다. 빈 값 제외·유지·오류와 중복 첫 행·마지막 행·유지·오류(중복이 있으면 실행 중단)는 filter_sort에 이미 정의된 정책입니다. 이 정책을 요청한 것은 기능 부족이 아닙니다. 사용자가 자료를 나중에 연결한다고 한 경우 파일 ID를 기능 판단 질문으로 반복하지 않습니다. 실행 전 필수 입력 검사는 후속 단계에 맡기며 비파일 업무에 표를 요구하지 않습니다.'
SYSTEM+='\napp_capabilities는 실제 구현·검증된 기능 목록입니다. 시뮬레이션 또는 미구현 계획이라고 추측하지 않습니다. 활성화·자료 연결·서버 기동은 지원 기능의 실행 전제이며 기능 부재가 아닙니다. 지원 기능과 미지원 동작이 섞이면 missing_actions에는 미지원 동작만 적습니다. 시·일·주·월 예약이나 활성화 자체를 부족 기능에 넣지 않습니다. 반면 yearly/cron은 현재 미지원으로 명시되어 있으므로 알려진 미지원이지 manual_review 대상이 아닙니다.'
SYSTEM+='\n조건끼리 서로 모순되거나(예: 같은 대상에 두 가지 정책) 기준·값·단위가 미정인 것은 기능 부족이 아니라 사용자가 정할 조건이므로 clarify입니다. 모순 해결, 기준 선택, 값 명시, 정책 우선순위 결정을 missing_actions에 적지 않고 missing_conditions에 적습니다. 등록 도구가 지원하는 작업(상수 비교 선별, 빈 값 정책, 첫 행·마지막 행 중복 제거, 파일 저장)을 조건이 모순이라는 이유로 미지원이라 하지 않습니다.'
SYSTEM+='\nGmail의 최근 메일 여러 건 요약은 email.list의 read_messages 동작으로 본문 documents를 읽고 compose_answer의 documents 입력에 연결합니다. 개별 ID·로컬 파일·read_document 단계가 필요하지 않습니다. 한국 시간 매일 오전 9시 전날 메일 요약은 등록된 gmail_previous_day_daily_summary.v1 조합으로 지원하며 query={{gmail.previous_day}}와 daily/Asia/Seoul/09:00 예약을 보존합니다. 전날 기간은 실행 시 계산하고 기본 조회 한도는 100건, 지정 가능한 최대치는 500건입니다. 제한을 넘는 메일은 포함되지 않을 수 있음을 안내합니다. 등록된 실제 동작 계약을 확인하며, Gmail 계정 연결 상태가 설계 입력에 없다는 이유로 기능을 missing_capability로 판정하지 않습니다. 연결은 실행 전 확인하며 사용자에게 모델이 연결 여부를 추측하거나 이미 연결됐다고 주장하지 않습니다. 메일 발송·다른 공급자·등록 범위 밖의 임의 자동 반복은 이 읽기 요약 조합에 포함되지 않습니다.'
SYSTEM+='\n제목·본문·열 이름·치환 문자열처럼 사용자가 지정한 입력값은 도구의 인자입니다. 그 값에 들어 있는 단어를 별도의 검토·완성·분석 업무로 만들지 않습니다. 제목과 본문을 지정한 메일 발송은 email.send의 등록된 발송 동작과 대조합니다. 기존 메일을 읽거나 검색하라는 요청이 있을 때만 email.list가 필요하며, Gmail 공급자를 명시한 것만으로 조회 업무를 추가하지 않습니다. 입력값이 아직 없으면 필요한 값만 질문하고, 계정 인증정보나 API 키는 채팅으로 요청하지 않습니다. 계정 연결은 앱의 연결 화면에서 확인하는 실행 전제입니다.'
DEMOS=[
 {'request':'매년 12월 31일 오전 10시 한국 시간에 표를 JSON 저장. 다른 주기로 바꾸지 마.', 'output':{'decision':'missing_capability','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':['연간 예약'],'reason':'표 저장은 지원하나 앱 예약은 hourly/daily/weekly/monthly만 지원하므로 연간 주기는 미지원.','user_quotes':['매년 12월 31일 오전 10시 한국 시간에 표를 JSON 저장. 다른 주기로 바꾸지 마.']}},
 {'request':'한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.', 'output':{'decision':'existing_tools','tool_ids':['read_table','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 주간 예약과 등록 표 읽기·저장 도구로 지원. 관리 자료 연결과 활성화는 후속 검사.','user_quotes':['한국 시간 매주 월요일 오전 10시에 표를 읽고 CSV로 저장해줘.']}},
 {'request':'한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.', 'output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'앱 일간 예약과 선별·저장 조합. 이미 정한 정책과 실행 전 파일 연결을 다시 묻지 않음.','user_quotes':['한국 시간 매일 11시에 수량이 12개 초과인 행만 JSON으로 저장. 빈 값 오류, 중복 유지. 파일은 나중에 연결.']}},
 {'request':'자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.','output':{'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],'missing_conditions':[],'missing_actions':[],'reason':'자료는 실행 전에 받고 등록 도구로 선별·파일 생성','user_quotes':['자료는 나중에 올릴게. 판매액이 27000원 이상인 행만 JSON 저장.']}},
 {'request':'가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.','output':{'decision':'clarify','tool_ids':['filter_sort'],'missing_conditions':['선별할 가격 기준과 포함 여부'],'missing_actions':[],'reason':'가격 기준은 사용자 결정 사항','user_quotes':['가격이 낮은 상품만 정리하고 싶은데 낮은 기준은 아직 미정이야.']}},
]

REQUEST_UPDATE_RULE = """대화는 순서대로 적용합니다. authoritative_request는 가장 최근 사용자 발언입니다.
최신 발언이 작업 일부를 취소하거나 범위를 좁히면 이전 요청의 해당 부분을 철회한 것으로 판단합니다.
예: '매출을 요약하고 이메일로 보내줘' 뒤 '메일은 취소', '이메일은 안 해', '요약만'이면 매출 요약만 남습니다.
취소된 이메일 발송이나 그 계정 연결을 missing_actions/missing_conditions에 남기지 않습니다.
반대로 '메일은 취소하지 마'는 기존 메일 작업을 유지합니다. 조회나 초안 작성만 요청한 경우 발송을 추가하지 않습니다.
'메일로는 보내줘'는 발송 요청입니다. 수신자 등 확인 질문에 답해도 철회되지 않은 요구와 조건은 유지합니다.
이전 assistant의 지원 불가 답변과 unverified_assessment는 판정 근거가 아닌 검토 대상입니다.
user_quotes에는 최신 변경 발언도 정확히 포함해 변경을 반영했다는 근거를 남깁니다.
남은 요청을 실제 카탈로그로 판단하며, 요청 축소만으로 지원 확정·실행·저장 허가를 부여하지 않습니다."""


_CHANNEL = r'(이메일|메일|e-?mail|슬랙|slack)'
_KEEP = re.compile(_CHANNEL + r'(발송|전송)?(?:은|는)?취소하지(?:마|마요|마세요|말아|말아줘)')
_SEND = re.compile(r'(?:다시)?' + _CHANNEL + r'(?:으?로(?:는|만)?)?(?:보내줘|보내주세요|발송해줘|전송해줘)')
_DEDUP = r'중복(?:제거|처리)?'
_JOIN = r'(?:과|와|이랑|랑|및|·)'
_CANCEL = re.compile(r'(?:' + _DEDUP + _JOIN + r')?' + _CHANNEL + r'(?:발송|전송)?(?:' + _JOIN + _DEDUP + r')?'
                     r'(?:은|는|을|를|도|으?로(?:는)?)?(?:취소(?:해(?:줘|주세요|요)?|할게(?:요)?|하고)?'
                     r'|안해|안함|빼(?:줘)?|보내지(?:는|도)?(?:마|말아줘|말고))')
_NO_SEND = re.compile(r'(?:(?:메일|이메일)?(?:발송|전송)(?:은|는)?하지|보내지)(?:는|도)?(?:마|말아줘|말고)')
_MAIL_WORK = re.compile(r'(?:이메일|메일|e-?mail)(?:은|는|을|를)?(?:목록|내용|본문|제목|초안)?(?:은|는|을|를)?'
                        r'(만)?(?:조회|검색|읽기|작성|준비)(만)?(?:해줘|해주세요|해)?$')


def _channel(name):
    return 'slack' if name.casefold() in ('슬랙', 'slack') else 'email'


def delivery_directives(state):
    """Replay explicit changes per channel. Other turns, including answers, leave them in force."""
    asked_to_send = set()
    directives = {}
    for message in _assessment_messages(state):
        if message['role'] != 'user':
            continue
        quote = message['content']
        # Column values inside quotation marks are data; punctuation there cannot start a directive.
        unquoted = re.sub(r"""(["']).*?\1""", lambda m: ' ' * len(m[0]), quote)
        named_channels = {_channel(name) for name in re.findall(_CHANNEL, unquoted, re.I)}
        for clause in re.split(r'[.!?~;,\n]+|(?<=취소하고)(?!\s*싶)\s*|(?<=말고)\s+', unquoted):
            text = ''.join(clause.casefold().split())
            keep, send, cancel = _KEEP.fullmatch(text), _SEND.fullmatch(text), _CANCEL.fullmatch(text)
            if keep:
                channel = _channel(keep[1])
                if not keep[2] and channel not in asked_to_send:
                    continue  # Keeping mail lookup or preparation does not request sending.
                asked_to_send.add(channel)
                channels, intent = [channel], 'retain_delivery'
            elif cancel:
                channels, intent = [_channel(cancel[1])], 'cancel_delivery'
            elif _NO_SEND.fullmatch(text) and len(named_channels or asked_to_send) == 1:
                channels, intent = named_channels or asked_to_send, 'cancel_delivery'
                # An explicitly non-sending mail task replaces the old referent of "메일은 취소하지 마".
                asked_to_send.difference_update(named_channels)
            elif text in {'요약만', '요약만해줘', '전송취소', '발송취소'}:
                channels, intent = asked_to_send, 'cancel_delivery'
            elif (mail_work := _MAIL_WORK.search(text)) and (
                    any(mail_work.groups()) or directives.get('email', {}).get('intent') == 'cancel_delivery'):
                # "Keep mail" now refers to lookup/preparation, even if an older turn once requested a send.
                asked_to_send.discard('email')
                channels, intent = ['email'], 'cancel_delivery'
            else:
                channels = {_channel(m[1]) for m in _external_delivery(clause)
                            if re.fullmatch(_CHANNEL, m[1], re.I)}
                if send:
                    channels.add(_channel(send[1]))
                asked_to_send.update(channels)
                # An initial compound request supplies context. A later explicit send
                # replaces a previous cancellation for that channel.
                if not send:
                    channels = channels.intersection(directives)
                intent = 'retain_delivery'
            for channel in channels:
                directives[channel] = {'channel': channel, 'intent': intent, 'quote': quote}
    return directives


def delivery_conflict(state, tool_ids):
    for channel, directive in delivery_directives(state).items():
        includes_send = channel == 'email' and 'email.send' in tool_ids
        if includes_send != (directive['intent'] == 'retain_delivery'):
            return directive
    return None


def guard_delivery_revision(result,state):
    if result['decision'] != 'existing_tools':return result
    directive = delivery_conflict(state, result['tool_ids'])
    if not directive:return result
    return {**result,'decision':'manual_review','model_decision':result['decision'],
            'request_revision_conflict':directive,
            'reason':'사용자의 발송 변경 요청과 선택한 도구가 일치하지 않아 재검토가 필요합니다.'}


_NEW_TASK = re.compile(
    r'^(?:(?:이제|지금부터)\s*)?(?:새(?:로운)?|다른|별개의)\s*(?:업무|작업|요청|질문)'
    r'(?:[.:：]|(?:으로|로|이야|입니다|예요)(?=[\s.!?,]|$))'
)
_MATERIAL = re.compile(
    r'(?<!\w)([가-힣A-Za-z_][\w.-]+?)(에서|으로부터|[을를의]|(?=\s+(?:파일|이미지|문서|자료|내역)))'
    r'(?=\s|$)'
)
_GENERIC_MATERIALS = {'파일', '이미지', '자료', '결과', '내용', '원본', '문서', '데이터', '항목', '합계', '평균', '금액', '목록'}
_ACTIONS = tuple(map(re.compile, (r'추출|뽑아|읽어|정리해', r'집계|합산|계산|합계|평균', r'비교|대조', r'선별|골라|필터', r'검수|대조 검토')))
_COMPARISON_INPUT = re.compile(r'[^.!?\n]{1,60}?(?:와|과)\s*(?:비교|대조)')
_REFERS_BACK = re.compile(r'거기|여기|원래|기존|다시|그대로|아까|앞서|이전|위의|대신|말고|바꿔|수정|취소|그리고|추가|이어서|함께|같이|그\s*(?:결과|자료|파일|표)|도(?=\s|$)')


def _assessment_messages(state):
    """Replay task boundaries so quotes and delivery decisions keep the same scope after a reply."""
    messages = state.get('messages', [])
    start = 0
    materials, actions = set(), set()
    for index, message in enumerate(messages):
        if message.get('role') != 'user':
            continue
        request = message['content'].strip()
        named = set()
        names_input = False
        for match in _MATERIAL.finditer(request):
            name, role = match.groups()
            if name in _GENERIC_MATERIALS:
                continue
            named.add(name)
            # Direct objects can be new output fields ("품목별 비용을 집계"), rather than
            # new input data. A source phrase or two explicit comparison inputs are stronger evidence.
            if role in ('에서', '으로부터', '') or (role in ('을', '를') and _COMPARISON_INPUT.match(request[match.end():])):
                names_input = True
        requested = {action for action in _ACTIONS if action.search(request)}
        # A long instruction may add a step. Without an explicit reset, require both
        # a new named input and a different kind of work before discarding evidence.
        independent = (materials and names_input and actions and requested and actions.isdisjoint(requested)
                       and not any(material in request for material in materials) and not _REFERS_BACK.search(request))
        if _NEW_TASK.match(request) or independent:
            start = index
            materials.clear()
            actions.clear()
        materials.update(named)
        actions.update(requested)
    return messages[start:]


def assess_capability(state,transport):
    faults=input_issues(state)
    if faults:return {'decision':'system_error','issues':faults,'model_calls':0,'allows_execution':False,'allows_generation':False}
    from agent_builder.design.temporal_context import temporal_context, TEMPORAL_INSTRUCTIONS
    request_clock=temporal_context(state)
    from agent_builder.design.request_meaning import sealed_meaning
    request_meaning=sealed_meaning(state)
    from agent_builder.design.gmail_summary import gmail_summary_assessment
    registered=gmail_summary_assessment(state)
    if registered:
        return validate_assessment_result(registered,state,model_calls=0)
    # The prompt, schema, initial decision and second-pass review must use the same evidence.
    state = {**state, 'messages': _assessment_messages(state)}
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
    directives = delivery_directives(state)
    from agent_builder.design.service_connections import service_connection_context
    messages=[{'role':'system','content':SYSTEM+'\n'+REQUEST_UPDATE_RULE+'\n'+TEMPORAL_INSTRUCTIONS}, {'role':'user','content':json.dumps({
        'messages':state['messages'],'resources':[{k:r[k] for k in ['id','kind','columns','text','pages','parser_status'] if k in r} for r in state.get('resources',[])],
        'temporal_context':request_clock,
        'pending_questions':state.get('pending_questions', []),
        'service_connections':service_connection_context(state),
        'request_meaning':request_meaning,
        'independent_examples':DEMOS,'phase':'design_not_execution','input_files_required_now':False,
        'app_capabilities':APP_CAPABILITIES,
        'tools':planner_catalog(TOOLS),
        'authoritative_request':user[-1],'active_delivery_directives':directives,'user_quote_catalog':user},ensure_ascii=False)}]
    reply=transport.invoke(messages)
    content=reply.get('content') if isinstance(reply,dict) else reply.content
    result=validate_assessment_result(Assessment.model_validate_json(content).model_dump(),state,model_calls=1)
    calls=1
    if result['decision']=='missing_capability':
        review_prompt='''이전 지원 판단은 미검증 후보입니다. 요청과 실제 tools 및 app_capabilities를 모두 다시 대조하여 최종 Assessment JSON 하나를 반환합니다.
가장 먼저 missing_actions 각 항목을 검토합니다. 앱의 hourly/daily/weekly/monthly 예약은 이미 구현된 지원 기능이므로 '주간 예약', '매주 ... 시각', '예약 활성화'를 부족 기능으로 적었다면 해당 항목을 제거합니다.
이 시점은 새 워크플로우 설계 전입니다. 특정 사용자의 활성화 설정이 아직 없다는 것은 당연하며 기능 부재가 아닙니다. 기존 활성화된 예약이 있어야 지원한다고 판단하지 않습니다.
요청에 미지원 외부 전송이 함께 있으면 외부 전송만 missing_actions에 남깁니다. 연간/cron은 실제 미지원으로 유지합니다.
누락된 입력 파일이나 실행 전 자료 연결은 기능 부족이 아닙니다. 도구 설명에 이미 있는 기능을 없다고 하지 마세요.
조건 사이의 모순, 미정 기준·값·단위, 정책 선택은 기능이 아니라 사용자 결정입니다. missing_actions에 이런 항목만 남는다면 clarify로 바꾸고 그 항목을 missing_conditions로 옮깁니다.
특히 reason에서 등록 도구 조합으로 가능하다고 설명하면서 decision을 missing_capability로 반환하면 모순입니다.
등록 도구로 요청의 모든 기능이 가능하면 existing_tools와 실제 tool_ids를 반환합니다. 필수 정책이 미정이면 clarify입니다.
표 join은 join_tables의 실제 등록 operation·입출력·정책과 대조합니다. 이메일·캘린더처럼 등록된 외부 동작은 기능 지원과 계정 연결 전제를 구분하고, Slack처럼 등록되지 않은 전송은 미지원으로 유지합니다. calculate_columns의 명시적 사칙연산·열 이름 변경과 text_transform의 문자열 바꾸기는 등록된 지원 기능입니다. 그 밖의 임의 셀 변환은 지원한다고 가정하지 마세요. 새로운 정책을 만들거나 요청을 바꾸지 마세요.
user_quotes는 원래 목록에서 정확히 선택합니다. 이 재검토도 실행/제작 허가가 아닙니다.'''
        reviewed=transport.invoke([{'role':'system','content':SYSTEM+'\n'+REQUEST_UPDATE_RULE+'\n'+TEMPORAL_INSTRUCTIONS+'\n'+review_prompt},
            messages[1],{'role':'user','content':json.dumps({'unverified_assessment':result,'authoritative_request':user[-1],'request_update_rule':REQUEST_UPDATE_RULE,'active_delivery_directives':directives,'task':'최신 사용자 변경을 먼저 적용한 뒤 남은 요청을 실제 카탈로그로 최종 재검토'},ensure_ascii=False)}])
        content=reviewed.get('content') if isinstance(reviewed,dict) else reviewed.content
        calls+=1
        result=validate_assessment_result(Assessment.model_validate_json(content).model_dump(),state,model_calls=calls)
    result=registered_policy_claims(reconcile_native_schedule(normalize_native_claims(result),state),state)
    result=external_delivery_claims(guard_delivery_revision(result,state),state)
    return {**validate_assessment_result(result,state,model_calls=calls),'model_calls':calls,'allows_execution':False,'allows_generation':False,'scope':'catalog assessment only; not condition READY'}
