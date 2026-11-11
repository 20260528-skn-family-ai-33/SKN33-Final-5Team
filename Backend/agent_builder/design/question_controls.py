"""Share answer controls across generated, pending and persisted questions.

Keep explicit contracts, and recover controls from alternatives and units the
question already states. Recovery proposes no new business values or defaults.
"""
from copy import deepcopy
import re
from pydantic import ValidationError

from contracts.models import QuestionResponse
from .question_language import KOREAN_QUESTION_INSTRUCTION

QUESTION_RESPONSE_INSTRUCTION = '''확인 질문은 답변 형식도 함께 선언합니다. question_responses는 questions와 같은 순서입니다.
한 가지 선택은 type=single_choice, 여러 항목을 고를 수 있으면 type=multi_choice로 두고 실제 선택지는 question_options에 작성합니다.
숫자 답변은 type=number로 두며 minimum/maximum/step/unit은 실제 업무의 범위·단위가 확정된 경우에만 지정합니다.
실행 시각처럼 시와 분을 함께 입력할 조건은 type=time으로 선언합니다. time은 09:00처럼 24시간 HH:MM 문자열이며 숫자형 number가 아닙니다. 시·분을 각각 다시 묻지 않습니다.
그 밖에는 type=text입니다. 선택지나 숫자 기준을 임의로 만들어 실제 업무 조건을 제안하지 않습니다. 질문이 없으면 빈 목록입니다.'''
QUESTION_RESPONSE_INSTRUCTION+='''
질문 본문에 A 또는 B, A 아니면 B, 예: A/B 등 선택 가능한 대안을 쓰면 question_options에 각 대안을 빠짐없이 작성합니다. 본문에만 선택지를 적고 type=text로 두지 않습니다.
예: "파일 형식은 CSV 또는 JSON 중 무엇으로 할까요?" → options=["CSV","JSON"], single_choice.
예: "포함할 항목을 선택해주세요: 이름, 날짜, 금액" → options=["이름","날짜","금액"], multi_choice.
예: "한 번에 몇 건을 처리할까요?" → options=[], number. "실행할 구체적인 시간은? (예:09:00)" → options=[], time.
설명이나 작성 지침처럼 고객이 자유롭게 써야 하는 질문만 text로 남깁니다.'''
QUESTION_RESPONSE_INSTRUCTION+='\n'+KOREAN_QUESTION_INSTRUCTION


def _declared_response(value):
    if not isinstance(value,dict):return None
    try:return QuestionResponse.model_validate(value).model_dump(exclude_none=True)
    except (ValueError,TypeError,ValidationError):return None


def _response(question,state,declared=None):
    response=_declared_response(question.get('response')) or _declared_response(declared)
    from .question_time import clock_question,clock_response
    response=clock_response(question,response)
    if response is None and not question.get('options') and clock_question(question):return {'type':'time'}
    if response:
        # Choices without actual answers remain open text inputs.
        if response['type'] in ('single_choice','multi_choice') and not question.get('options'):
            return {'type':'text'}
        return response
    key=question.get('key')
    bounds={'schedule.hour':(0,23,'시'),'schedule.minute':(0,59,'분'),
            'schedule.monthday':(1,31,'일')}
    if key in bounds:
        minimum,maximum,unit=bounds[key]
        return {'type':'number','minimum':minimum,'maximum':maximum,'step':1,'unit':unit}
    if question.get('options'):return {'type':'single_choice'}
    if key=='filter.value':
        from agent_builder.conditions.input_resolution import column_unit,numeric_column
        slots=(state.get('condition_ledger') or {}).get('slots',{})
        field=slots.get('filter.field',{}).get('value')
        operator=slots.get('filter.operator',{}).get('value')
        if field and (column_unit(field) or numeric_column(state,field) or operator in ('gt','gte','lt','lte')):
            return {'type':'number'}
    return {'type':'text'}


_QUOTED = re.compile(r"['\"‘“`「]([^'\"’”`」\n]+)['\"’”`」]")
_MULTIPLE = re.compile(r'복수|다중|중복\s*선택|여러\s*(?:개|항목|가지|값)|모두\s*선택|포함할\s*(?:항목|열|필드)',re.I)
_SINGLE = re.compile(r'한\s*(?:가지|개)|하나\s*만|단일\s*선택|택일',re.I)
_ALTERNATIVE = re.compile(r'\s*(?:아니면|또는|혹은|\bor\b)\s*',re.I)
_LIST_SEPARATOR = re.compile(r'\s*[,/·|]\s*|\s+(?:또는|혹은)\s+')


def _clean_option(value):
    value=value.strip(" \t\n,.;:?!。？'\"‘’“”`「」")
    value=re.sub(r'\s+중(?:에서)?\s+(?:무엇|어떤|어느|하나|한\s*(?:가지|개)|선택).*$','',value)
    value=re.sub(r'\s*(?:등(?:등)?|중에서|중)\s*$','',value).strip()
    value=re.sub(r'(?:인가요|일까요|입니까|일지|으로\s*할까요|로\s*할까요|을까요|를까요)[?？.! ,]*$','',value).strip()
    return value.strip(" \t,.;:?!'\"‘’“”`「」")


def _option_list(values):
    values=list(dict.fromkeys(_clean_option(value) for value in values))
    values=[value for value in values if value and value not in ('직접 입력','기타')]
    if 2<=len(values)<=12 and all(len(value)<=100 and not re.search(r'https?://|[\\\n]',value) for value in values):return values
    return []


def _stated_options(text):
    """Only alternatives already offered in the question; no domain recipes."""
    # Parenthesized examples are a common legacy form of a choice contract.
    examples=re.findall(r'[（(]\s*(?:예(?:시)?\s*[:：]|예를\s*들어\s*)([^()（）]+)[)）]',text)
    for example in examples:
        options=_option_list(_LIST_SEPARATOR.split(example))
        if options:return options
    # A heading outside parentheses is context, not part of an answer label.
    for candidate in re.findall(r'[（(]([^()（）]+)[)）]',text):
        if _ALTERNATIVE.search(candidate):
            options=_option_list(_ALTERNATIVE.split(candidate))
            if options:return options
    if _ALTERNATIVE.search(text):
        parts=_ALTERNATIVE.split(text)
        if 2<=len(parts)<=4:
            for index,part in enumerate(parts):
                quoted=_QUOTED.findall(part)
                if quoted:parts[index]=quoted[-1] if index==0 else quoted[0]
            if not _QUOTED.findall(_ALTERNATIVE.split(text)[0]):
                parts[0]=re.sub(r'^.*?(?:형식|방식|범위|정책|기준|종류|대상|결과|항목|열|필드)(?:은|는)\s*','',parts[0])
            options=_option_list(parts)
            if options:return options
    # Delimited lists must be attached to an explicit selection request.
    if re.search(r'선택|고를|고르|포함할|어떤\s*형식|무슨\s*형식',text):
        candidates=re.findall(r'[（(]([^()（）]+)[)）]',text)
        if re.search(r'[:：]',text):candidates.append(re.split(r'[:：]',text,maxsplit=1)[1])
        for candidate in candidates:
            options=_option_list(_LIST_SEPARATOR.split(candidate))
            if options:return options
    return []


def _numeric_control(text):
    # A compound request needs its own fields; never squeeze multiple answers
    # into a single numeric input merely because it contains a number word.
    if re.search(r'(?:와|과|하고|및).{0,15}(?:어떤|무슨|얼마|몇)',text):return None
    if re.search(r'몇\s*(?:건|개|명|회|원|초|분|시간|일|퍼센트|%)|(?:건수|개수|수량|금액|비율|최대\s*건수|조회\s*한도|임계값)(?:은|는|을|를|이|가)?\s*(?:얼마|몇|입력|알려|정해|설정)',text):
        return {'type':'number'}
    return None


def infer_question_control(question,state):
    """Complete one control without changing its identity, wording or meaning."""
    question=deepcopy(question)
    if question.get('options'):question['options']=list(dict.fromkeys(question['options']))
    if question.get('options') and any(re.search(r'[()（）]|알려주세요',option) for option in question['options']):
        repaired=_stated_options(question.get('text',''))
        if repaired:question['options']=repaired
    response=_response(question,state)
    from .question_time import clock_question
    if response['type'] in ('text','number','time') and clock_question(question):
        question['response']={'type':'time'}
        return question
    if response['type']=='text' and not question.get('options'):
        numeric=_numeric_control(question.get('text',''))
        if numeric:
            question['response']=numeric
            return question
        options=_stated_options(question.get('text',''))
        if options:question['options']=options
    if question.get('options') and (response['type']=='text' or not _declared_response(question.get('response'))):
        text=question.get('text','')
        response={'type':'multi_choice' if _MULTIPLE.search(text) and not _SINGLE.search(text) else 'single_choice'}
    question['response']=response
    return question


def complete_questions(questions,state):
    from .execution_mode import filter_execution_questions
    return [infer_question_control(question,state) for question in filter_execution_questions(questions,state)]


def complete_draft_question_controls(draft,state):
    """Persist the same controls that public snapshots and answers validate."""
    if not isinstance(draft,dict):return draft
    from .execution_mode import filter_draft_execution_questions
    draft=filter_draft_execution_questions(draft,state)
    draft=deepcopy(draft)
    options=draft.get('question_options') or []
    responses=draft.get('question_responses') or []
    questions=[]
    for index,text in enumerate(draft.get('questions',[])):
        question={'text':text,'options':options[index] if index<len(options) else []}
        if index<len(responses) and responses[index]:question['response']=responses[index]
        questions.append(infer_question_control(question,state))
    draft['question_options']=[question.get('options',[]) for question in questions]
    draft['question_responses']=[question['response'] for question in questions]
    return draft


def project_question_controls(state,pending_questions=None):
    """Add UI metadata to a public snapshot without modifying its saved checkpoint."""
    from .execution_mode import project_execution_questions,filter_execution_questions
    state=project_execution_questions(state)
    pending=state.get('pending_questions',[]) if pending_questions is None else filter_execution_questions(pending_questions,state)
    draft=state.get('draft')
    draft_questions=draft.get('questions',[]) if isinstance(draft,dict) else []
    supplied=(draft.get('question_responses') or []) if isinstance(draft,dict) else []
    options=(draft.get('question_options') or []) if isinstance(draft,dict) else []
    projected=[]
    for original in pending:
        question={**original}
        index=next((i for i,text in enumerate(draft_questions) if text==question.get('text')),None)
        declared=supplied[index] if index is not None and index<len(supplied) else None
        if not question.get('options') and index is not None and index<len(options):
            question['options']=options[index]
        if not question.get('response') and declared:question['response']=declared
        projected.append(infer_question_control(question,state))
    if not isinstance(draft,dict):return {'draft':draft,'pending_questions':projected}
    responses=[];projected_options=[]
    for index,text in enumerate(draft_questions):
        question=next((q for q in projected if q.get('text')==text),None)
        if question is None:question={'text':text,'options':options[index] if index<len(options) else []}
        if not question.get('response') and index<len(supplied) and supplied[index]:question={**question,'response':supplied[index]}
        question=infer_question_control(question,state)
        responses.append(question['response']);projected_options.append(question.get('options',[]))
    return {'draft':{**draft,'question_options':projected_options,'question_responses':responses},'pending_questions':projected}
