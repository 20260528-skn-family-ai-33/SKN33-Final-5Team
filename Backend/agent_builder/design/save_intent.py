"""A chat message that only asks to save the reviewed draft is the save button, not a new request.

Before this, "확인완료. 저장" resumed the approval wait as a change request: every
check and the planner ran again, the same draft came back, and nothing was saved.
Only whole messages made of acknowledgement words plus a save verb qualify, so a
change such as "저장 형식을 JSON으로 바꿔줘" is still handled as a request.
"""
import re

_ACK = r'(?:네|예|응|넵|좋아(?:요)?|좋습니다|확인(?:\s*완료|했어(?:요)?|했습니다|했음)?|완료|됐어(?:요)?|ok|오케이|문제\s*없어(?:요)?|이대로|그대로|맞아(?:요)?)'
_SAVE = (r'저장(?:\s*(?:해\s*(?:줘|주세요|줄래|요)?|할게(?:요)?|하자|합니다|해도\s*돼(?:요)?|하기|부탁(?:해요|합니다|드려요)?))?'
         r'|저장\s*버튼(?:\s*눌러\s*(?:줘|주세요))?')
_SEP = r'[\s.,!~^]*'
_SAVE_ONLY = re.compile(r'^' + _SEP + r'(?:' + _ACK + _SEP + r')*(?:' + _SAVE + r')' + _SEP + r'$', re.I)


def is_save_request(text):
    return isinstance(text, str) and len(text) <= 60 and bool(_SAVE_ONLY.match(text.strip()))


def blocked_save_reply(state):
    """Why the draft cannot be saved yet, in the user's terms; never re-plans."""
    if state.get('status') == 'saved':
        return '이미 저장한 워크플로우입니다. 바꾸려면 설정 수정을 시작해 주세요.'
    if (state.get('draft') or {}).get('questions') or state.get('pending_questions'):
        return '아직 답하지 않은 확인 질문이 있어 저장할 수 없습니다. 질문에 먼저 답해 주세요.'
    issues = [issue for issue in state.get('issues') or [] if isinstance(issue, str) and issue.strip()]
    if issues:
        return '초안을 검증하지 못해 아직 저장할 수 없습니다. 확인할 내용: ' + ' / '.join(issues[:2])
    if not (state.get('draft') or {}).get('workflow'):
        return '저장할 워크플로우 초안이 아직 없습니다. 처리할 업무를 먼저 설명해 주세요.'
    return '아직 저장할 수 있는 상태가 아닙니다. 업무 조건을 확인한 뒤 다시 시도해 주세요.'
