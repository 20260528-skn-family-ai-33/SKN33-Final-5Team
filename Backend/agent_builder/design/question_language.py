"""Keep model-written clarification wording Korean without changing source data."""
import re
from copy import deepcopy

KOREAN_QUESTION_INSTRUCTION='질문·항목 제목·필요한 이유는 자연스러운 한국어로 작성합니다. 중국어·한자를 섞지 않습니다. 시각은 "몇 시 몇 분"으로 씁니다. 사용자가 제공한 고유명사·자료 열 이름·선택값은 원문을 보존합니다.'
_HAN=re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+')
_CLOCK=re.compile(r'몇\s*[시時]|시각|실행\s*(?:시간|時間)|예약\s*(?:시간|時間)|HH\s*:\s*MM',re.I)
_LANGUAGE_NOTICE='확인 질문을 한국어로 정리하는 중 문제가 생겼습니다. 입력한 조건은 보존되어 있으니 같은 조건으로 다시 시도해주세요.'


def _sources(state):
    return '\n'.join(message.get('content','') for message in state.get('messages',[]) if message.get('role')=='user')+'\n'+'\n'.join(
        str(asset.get(field,'')) for asset in state.get('resources',[]) for field in ('name','columns','text','preview','profile'))


def _unexpected(text,sources):
    return isinstance(text,str) and any(word not in sources for word in _HAN.findall(text))


def _normalize(text,sources):
    if not isinstance(text,str):return text
    protected={}
    def protect(match):
        if match[0] not in sources:return match[0]
        token='__SOURCE_NAME_'+str(len(protected))+'__';protected[token]=match[0];return token
    text=korean_clock_text(_HAN.sub(protect,text))
    for token,original in protected.items():text=text.replace(token,original)
    return text


def korean_clock_text(text):
    if not isinstance(text,str) or not _CLOCK.search(text):return text
    for source,target in (('几分钟','몇 분'),('幾分鐘','몇 분'),('几分','몇 분'),('幾分','몇 분'),
                          ('几时','몇 시'),('幾時','몇 시'),('几点','몇 시'),('幾點','몇 시'),
                          ('分钟','분'),('分鐘','분'),('時間','시간'),('时间','시간')):
        text=text.replace(source,target)
    text=re.sub(r'몇\s*時','몇 시',text)
    text=re.sub(r'몇\s*分','몇 분',text)
    return re.sub(r'몇\s*시\s*몇\s*분','몇 시 몇 분',text)


def normalize_question_language(state):
    """Project old and new question copies alike; never rewrite the user's turn."""
    result={**state,'messages':[dict(message) for message in state.get('messages',[])],
            'pending_questions':[dict(question) for question in state.get('pending_questions',[])]}
    sources=_sources(state);draft=result.get('draft');blocked=False
    if isinstance(draft,dict):
        options=draft.get('question_options') or [];responses=draft.get('question_responses') or [];kept=[]
        for index,text in enumerate(draft.get('questions',[])):
            record={'text':text,'options':options[index] if index<len(options) else [],
                    'response':responses[index] if index<len(responses) else None}
            try:validate_question_language([record],state)
            except ValueError:blocked=True;continue
            kept.append((index,record['text']))
        result['draft']={**draft,'questions':[text for _,text in kept],
            'question_options':[options[index] if index<len(options) else [] for index,_ in kept],
            'question_responses':[responses[index] if index<len(responses) else None for index,_ in kept]}
    visible=[]
    for question in result.get('pending_questions',[]):
        try:validate_question_language([question],state)
        except ValueError:blocked=True;continue
        visible.append(question)
    result['pending_questions']=visible
    last_assistant=max((i for i,message in enumerate(result['messages']) if message.get('role')=='assistant'),default=-1)
    for index,message in enumerate(result.get('messages',[])):
        if message.get('role')=='assistant':
            lines=[]
            for line in message.get('content','').split('\n'):
                line=_normalize(line,sources)
                if _unexpected(line,sources):
                    if index==last_assistant:blocked=True
                    line=_LANGUAGE_NOTICE
                if not lines or line!=_LANGUAGE_NOTICE or lines[-1]!=line:lines.append(line)
            message['content']='\n'.join(lines)
    if blocked:
        result['requirement_failure']={'kind':'question_language_error','retryable':True}
        if last_assistant>=0:
            if _LANGUAGE_NOTICE not in result['messages'][last_assistant]['content']:
                result['messages'][last_assistant]['content']+='\n'+_LANGUAGE_NOTICE
        else:result['messages'].append({'role':'assistant','content':_LANGUAGE_NOTICE})
    return result


def validate_question_language(questions,state):
    sources=_sources(state)
    for question in questions:
        if not isinstance(question,dict):continue
        for field in ('text','group','reason'):
            text=question.get(field)
            if not isinstance(text,str):continue
            question[field]=_normalize(text,sources)
            if _unexpected(question[field],sources):
                raise ValueError(KOREAN_QUESTION_INSTRUCTION)
        if any(_unexpected(option,sources) for option in question.get('options',[])):
            raise ValueError(KOREAN_QUESTION_INSTRUCTION)
        if _unexpected((question.get('response') or {}).get('unit'),sources):
            raise ValueError(KOREAN_QUESTION_INSTRUCTION)


def validate_draft_question_language(draft,state):
    """Apply the same guard to both planners and execution-time question nodes."""
    result=deepcopy(draft)
    options=result.get('question_options') or []
    responses=result.get('question_responses') or []
    records=[{'text':text,'options':options[index] if index<len(options) else [],
              'response':responses[index] if index<len(responses) else None}
             for index,text in enumerate(result.get('questions',[]))]
    validate_question_language(records,state)
    result['questions']=[record['text'] for record in records]
    for node in (result.get('workflow') or {}).get('nodes',[]):
        if node.get('tool')=='ask_user' and isinstance(node.get('settings'),dict):
            text=node['settings'].get('question')
            if text:
                record={'text':text};validate_question_language([record],state);node['settings']['question']=record['text']
    return result
