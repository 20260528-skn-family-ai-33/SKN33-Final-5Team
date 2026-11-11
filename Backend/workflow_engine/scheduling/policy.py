"""Application-owned schedule settings shared by design and execution."""
import re

WORKFLOW_TIMEZONE='Asia/Seoul'


def fixed_schedule(spec):
    return {**spec,'timezone':WORKFLOW_TIMEZONE} if isinstance(spec,dict) else spec


def timezone_question(question):
    key=str(question.get('key','')).casefold().replace('_','.')
    if key=='timezone' or key.endswith('.timezone'):return True
    text=str(question.get('text',''))
    return bool(re.search(r'(?:시간대|time\s*zone|timezone)',text,re.I) and
                not re.search(r'주기|몇\s*시|실행\s*시각|실행\s*시간|요일|일자',text))


def project_fixed_timezone(state):
    """Adapt old checkpoints as well as new drafts to the application setting."""
    pending=[question for question in state.get('pending_questions',[]) if not timezone_question(question)]
    draft=state.get('draft')
    result={**state,'pending_questions':pending}
    if isinstance(draft,dict):
        questions=draft.get('questions',[])
        keep=[index for index,text in enumerate(questions) if not timezone_question({'text':text})]
        options=draft.get('question_options') or [];responses=draft.get('question_responses') or []
        result['draft']={**draft,'questions':[questions[index] for index in keep],
            'question_options':[options[index] if index<len(options) else [] for index in keep],
            'question_responses':[responses[index] if index<len(responses) else None for index in keep]}
        if draft.get('schedule'):result['draft']['schedule']=fixed_schedule(draft['schedule'])
    return result
