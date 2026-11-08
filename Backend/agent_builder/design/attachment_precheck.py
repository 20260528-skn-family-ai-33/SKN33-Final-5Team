"""Ask for the file without a model call when the request points at an upload that is not attached.

Whether files are attached is a fact the code already knows. Only the wording check
(``refers_to_attached_file``) is a guess; it is a small keyword rule that can later be
replaced by a trained lightweight classifier without touching the graph.
"""
import os
import re

# Past-tense reference to something the user believes is already attached.
_UPLOADED = re.compile(r'(올려\s*준|올려\s*드린|올린|첨부(한|된|해\s*준)|첨부\s*파일|첨부파일|업로드(한|된|해\s*준)|보내\s*준|보낸(?!\s*사람)|붙여\s*준|넣어\s*준|넣은)')
_OBJECT = re.compile(r'(파일|이미지|사진|영수증|문서|자료|스캔|엑셀|pdf|csv|xlsx|표)', re.IGNORECASE)
_EMAIL_ATTACHMENT = re.compile(r'(?:Gmail|메일)(?:의|에|에서)\s*(?:본문\s*(?:과|및)\s*)?첨부(?:한|된)?\s*파일', re.IGNORECASE)
# The file is meant for a later run, not for this design conversation.
_LATER = re.compile(r'(실행\s*(할\s*때|시)|나중에|매일|매주|매월|예약|올리면|올릴|업로드하면|넣으면|넣을|받으면|들어오면)')

QUESTION = '분석할 파일을 먼저 올려 주세요. 실행할 때 파일을 넣을 계획이면 "실행할 때 넣을게요"라고 답해 주세요.'
EXPLANATION = '요청에 올린 파일이 언급됐지만 연결된 파일이 없습니다.'


def refers_to_attached_file(text):
    text=_EMAIL_ATTACHMENT.sub('메일',text)
    imminent=bool(re.search(r'올려\s*줄\s*(?:게|테)|올릴\s*테|첨부해\s*줄\s*(?:게|테)',text))
    later=bool(re.search(r'실행\s*(?:할\s*때|시)|나중에|예약',text))
    return bool(_OBJECT.search(text) and ((imminent and not later) or (_UPLOADED.search(text) and not _LATER.search(text))))


def missing_attachment_reply(state):
    """Return a clarification result, or None to continue with the model planner."""
    if os.getenv('A4B_ATTACHMENT_PRECHECK', 'on') == 'off' or state.get('resources'):
        return None
    users = [m for m in state.get('messages', []) if m['role'] == 'user']
    if not users or not refers_to_attached_file(users[-1]['content']):
        return None
    request = users[-1]['content']
    draft = dict(name='파일 확인', purpose=request[:2000], instructions=request[:12000], tool_ids=[], document_ids=[],
                 requirements=[dict(description='사용자가 올린 파일을 기준으로 처리', source_id='user', quote=request[:2000])],
                 questions=[QUESTION], question_options=[], unsupported=[], explanation=EXPLANATION,
                 execution_plan=dict(filters=[], sort=[], match='all', missing_policy='ask', duplicate_policy='keep',
                                     duplicate_field=None, output_format='csv'))
    return {'messages': state['messages'] + [{'role': 'assistant', 'content': EXPLANATION + '\n• ' + QUESTION}],
            'draft': draft, 'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '첨부 파일 확인', 'status': 'needs_input',
                                                'detail': '연결된 파일이 없어 모델을 호출하지 않고 업로드를 요청했습니다.'}]}
