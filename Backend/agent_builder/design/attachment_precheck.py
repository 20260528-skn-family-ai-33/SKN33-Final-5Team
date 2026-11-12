"""Ask for the file without a model call when the request points at an upload that is not attached.

Whether files are attached is a fact the code already knows. Only the wording check
(``refers_to_attached_file``) is a guess; it is a small keyword rule that can later be
replaced by a trained lightweight classifier without touching the graph.
"""
import os
from agent_builder.design import file_timing

QUESTION = '분석할 파일을 먼저 올려 주세요. 실행할 때 파일을 넣을 계획이면 "실행할 때 넣을게요"라고 답해 주세요.'
EXPLANATION = '요청에 올린 파일이 언급됐지만 연결된 파일이 없습니다.'


def refers_to_attached_file(text):
    return file_timing.refers_to_attached_file(text)


def _needed_now(users):
    """Files the latest turn needs attached now, judged per mention, not per message."""
    latest = users[-1]['content']
    mentions = file_timing.now_mentions(latest)
    if not mentions:
        return [], set()
    # Runtime files recorded in earlier turns and in other clauses of this turn.
    deferred = file_timing.deferred_subjects([m['content'] for m in users])
    immediate = bool(file_timing.NEW_OR_IMMEDIATE_UPLOAD.search(file_timing.strip_quotes(latest)))
    needed = [m for m in mentions if immediate or not file_timing.same_subject(m, deferred)]
    if immediate:
        # "방금 첨부한 계약서 ... 견적서는 실행할 때": only the explicitly deferred file stays a runtime input.
        current = {s for c, subjects in file_timing.clause_choices(latest) if c == 'execution' for s in subjects}
        needed = [m for m in mentions if not file_timing.same_subject(m, current)] if current else mentions
        deferred = current
    return needed, deferred


def missing_attachment_reply(state):
    """Return a clarification result, or None to continue with the model planner."""
    if os.getenv('A4B_ATTACHMENT_PRECHECK', 'on') == 'off' or state.get('resources'):
        return None
    users = [m for m in state.get('messages', []) if m['role'] == 'user']
    if not users:
        return None
    needed, deferred = _needed_now(users)
    if not needed:
        return None
    request = users[-1]['content']
    requirements = [dict(description='사용자가 올린 파일을 기준으로 처리', source_id='user', quote=request[:2000])]
    if deferred:
        # Keep the other file as an execution input instead of asking for it now.
        requirements.append(dict(description='실행할 때 받는 입력: ' + ', '.join(sorted(deferred)), source_id='user', quote=request[:2000]))
    draft = dict(name='파일 확인', purpose=request[:2000], instructions=request[:12000], tool_ids=[], document_ids=[],
                 requirements=requirements,
                 questions=[QUESTION], question_options=[], unsupported=[], explanation=EXPLANATION,
                 execution_plan=dict(filters=[], sort=[], match='all', missing_policy='ask', duplicate_policy='keep',
                                     duplicate_field=None, output_format='csv'))
    return {'messages': state['messages'] + [{'role': 'assistant', 'content': EXPLANATION + '\n• ' + QUESTION}],
            'draft': draft, 'confirmed': False, 'saved_agent_id': None,
            'trace': state.get('trace', []) + [{'node': 'conversation', 'label': '첨부 파일 확인', 'status': 'needs_input',
                                                'detail': '연결된 파일이 없어 모델을 호출하지 않고 업로드를 요청했습니다.'}]}
