"""Block automatic schedule restoration after an unresolved user revision."""
import re

_CANCEL = re.compile(r'예약\s*(?:은|을)?\s*(?:없이|취소(?!하지\s*(?:마|말|않))|하지\s*마|안\s*해)|이번\s*(?:한\s*번|한번|1회)\s*만')
_DESTINATION = re.compile(r'(?:결과|보고서|파일)?\s*(?:보관|저장)\s*(?:위치|장소)|서버\s*보관함|reports', re.I)
_UNDECIDED = re.compile(r'미정|모르|나중|먼저\s*물어|정하지\s*않')
_REFUSE = re.compile(r'저장하지\s*마|보관하지\s*마|저장\s*금지|보관\s*금지')
_RENEW = re.compile(
    r'(?:매일|매주|daily|weekly).*(?:예약|실행|저장|보내)(?:해|하|해줘|줘|해주|할)'
    r'|예약(?:을|은)?\s*(?:다시|재개).*?(?:해|하|줘)', re.I)
_DESTINATION_SELECTED = re.compile(
    r'(?:서버\s*보관함|reports)(?:에|으로|로|을|를)?\s*'
    r'(?:저장|보관|선택|확정|할게|해줘|하겠|으로\s*할)', re.I)


def schedule_request_issues(state):
    ledger = state.get('condition_ledger') or {}
    if 'schedule' not in ledger.get('operations', []) and not (state.get('draft') or {}).get('schedule'):
        return []
    cancelled = False
    destination_pending = False
    for message in state.get('messages', []):
        if message.get('role') != 'user':
            continue
        text = re.sub(r"""(["']).*?\1""", '', message['content'])
        if _CANCEL.search(text):
            cancelled = True
        elif _RENEW.search(text):
            cancelled = False
        if _DESTINATION.search(text):
            if _UNDECIDED.search(text) or _REFUSE.search(text):
                destination_pending = True
            elif _DESTINATION_SELECTED.search(text):
                destination_pending = False
    issues = []
    if cancelled:
        issues.append('예약을 철회한 최신 요청이 있어 예약 조건을 다시 확인해야 합니다.')
    if destination_pending:
        issues.append('결과 저장 위치가 미정이거나 서버 보관을 거절했습니다. 저장 위치를 먼저 확인해야 합니다.')
    return issues
