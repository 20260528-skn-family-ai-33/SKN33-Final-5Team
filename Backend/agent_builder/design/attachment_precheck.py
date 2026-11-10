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
_DEFER_UPLOAD = re.compile(
    r'실행\s*(?:할\s*때|시)\s*(?:(?:해당\s*)?(?:파일|자료|문서|PDF|CSV)(?:은|는|을|를)?\s*)?'
    r'(?:넣을게(?:요)?|올릴게(?:요)?|제공할게(?:요)?|업로드할게(?:요)?|첨부할게(?:요)?|'
    r'(?:넣|올리|제공하|업로드하|첨부하)겠(?:습니다|어요))', re.IGNORECASE)
_NEW_OR_IMMEDIATE_UPLOAD = re.compile(
    r'(?:지금|방금)\s*(?:새로\s*)?(?:첨부|업로드|올리|올렸|보내|보냈|넣)|'
    r'실행\s*(?:할\s*때|시)\s*(?:말고|가?\s*아니라).{0,50}지금|'
    r'(?:새로|추가로)\s*(?:첨부|업로드|올린|올려|보낸)|'
    r'(?:새(?:로운)?|다른|추가)\s*(?:파일|자료|문서|PDF|CSV|XLSX|엑셀|영수증|사진|이미지)', re.IGNORECASE)

QUESTION = '분석할 파일을 먼저 올려 주세요. 실행할 때 파일을 넣을 계획이면 "실행할 때 넣을게요"라고 답해 주세요.'
EXPLANATION = '요청에 올린 파일이 언급됐지만 연결된 파일이 없습니다.'


def refers_to_attached_file(text):
    text=_EMAIL_ATTACHMENT.sub('메일',text)
    imminent=bool(re.search(r'올려\s*줄\s*(?:게|테)|올릴\s*테|첨부해\s*줄\s*(?:게|테)',text))
    later=bool(re.search(r'실행\s*(?:할\s*때|시)|나중에|예약',text))
    return bool(_OBJECT.search(text) and ((imminent and not later) or (_UPLOADED.search(text) and not _LATER.search(text))))


def _upload_references(text):
    """Small surface references, only for retaining an explicit runtime-file choice."""
    text = _EMAIL_ATTACHMENT.sub('메일', text)
    result = set()
    for marker in _UPLOADED.finditer(text):
        # "첨부파일" is itself an upload marker. Its "파일" must not hide
        # the named document that follows, such as 계약서 PDF / 견적서 PDF.
        fragment = text[marker.end():marker.end() + 60]
        target = _OBJECT.search(fragment)
        if target is None:
            if _OBJECT.search(marker.group(0)):
                result.add('파일')
            continue
        subject = fragment[:target.end()]
        if _OBJECT.search(marker.group(0)):
            subject = re.sub(r'^(?:인|은|는|의|을|를)\s+', '', subject)
        subject = re.sub(r'\s+', '', subject).casefold() or target.group(0).casefold()
        result.add(subject)
    return result


def _same_uploads(current, deferred):
    if not current or not deferred:
        return False
    for subject in current:
        if subject in deferred:
            continue
        # A later "the PDF/file" can refer back to one identified deferred file,
        # but must not choose silently between multiple pending files.
        if subject in ('파일', '자료', '문서') and len(deferred) == 1:
            continue
        if subject in ('pdf', 'csv', 'xlsx', '엑셀', '이미지', '사진', '표') and sum(
                previous.endswith(subject) for previous in deferred) == 1:
            continue
        return False
    return True


def _deferred_upload_applies(users):
    previous_refs = set()
    deferred = set()
    for message in users[:-1]:
        text = message['content']
        refs = _upload_references(text)
        if _NEW_OR_IMMEDIATE_UPLOAD.search(text):
            deferred = set()
            previous_refs = set()
        if _DEFER_UPLOAD.fullmatch(text.strip().rstrip('.!~')):
            deferred = refs or previous_refs
        elif refs and deferred and not _same_uploads(refs, deferred):
            deferred = set()
        if refs:
            previous_refs = refs
    latest = users[-1]['content']
    return (not _NEW_OR_IMMEDIATE_UPLOAD.search(latest)
            and _same_uploads(_upload_references(latest), deferred))


def missing_attachment_reply(state):
    """Return a clarification result, or None to continue with the model planner."""
    if os.getenv('A4B_ATTACHMENT_PRECHECK', 'on') == 'off' or state.get('resources'):
        return None
    users = [m for m in state.get('messages', []) if m['role'] == 'user']
    if not users or not refers_to_attached_file(users[-1]['content']):
        return None
    if _deferred_upload_applies(users):
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
