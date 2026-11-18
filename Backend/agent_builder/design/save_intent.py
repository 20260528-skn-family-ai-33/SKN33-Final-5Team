"""Recognize chat about saving the current draft by its complete utterance."""
import re
import unicodedata

_ACK = r'(?:네|예|응|넵|좋아(?:요)?|좋습니다|확인(?:\s*완료|했어(?:요)?|했습니다|했음)?|완료|됐어(?:요)?|ok|오케이|문제\s*없어(?:요)?|이대로|그대로|맞아(?:요)?)'
_SEPARATORS = ' \t\r\n.,!?~^。…'
_ACK_PREFIX = re.compile(_ACK + r'(?=$|[\s.,!?~^。…])', re.I)
_TARGET = r'(?:(?:이|그|현재)\s*)?(?:워크플로우|초안)(?:을|를|은|는)?\s*'
_SAVE_ACTION = (
    r'해\s*(?:줘|주세요|줄래(?:요)?|요)?|할게(?:요)?|하자|합니다|하기|'
    r'부탁(?:해요|합니다|드려요)?')
_SAVE_QUESTION = (
    r'해도\s*(?:돼(?:요)?|되나요|되겠죠|됩니까)|할\s*수\s*있(?:어(?:요)?|나요|을까요|습니까)|'
    r'할까요|하나요|하죠|합니까')
_SAVE_BUTTON = r'저장\s*버튼(?:을|은)?(?:\s+(?:눌러\s*(?:줘|주세요)|어디(?:에)?\s*(?:있어(?:요)?|있나요)?))?'
_SAVE_ONLY = re.compile(
    rf'(?:{_TARGET})?(?:(?:어떻게\s+)?저장\s*(?:{_SAVE_ACTION}|{_SAVE_QUESTION})?|{_SAVE_BUTTON})')


def is_save_request(text):
    if not isinstance(text, str) or len(text) > 4000:
        return False
    rest = unicodedata.normalize('NFKC', text).strip(_SEPARATORS)
    while acknowledgement := _ACK_PREFIX.match(rest):
        rest = rest[acknowledgement.end():].lstrip(_SEPARATORS)
    # Format, destination and workflow edits do not fit this grammar.
    return _SAVE_ONLY.fullmatch(rest) is not None


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
