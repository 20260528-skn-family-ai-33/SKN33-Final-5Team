"""Attaching a file never makes the assistant speak or run; the next user message does."""
import tempfile

from service.worker import handle
from tests.design.test_runtime_file_questions import question_draft

DOC = {'name': '계약서.txt', 'kind': 'document', 'format': 'txt', 'text': '제1조 해지 조항', 'pages': ['제1조 해지 조항'],
       'version': 1, 'hash': 'h', 'profile': {}}


def _session(monkeypatch):
    for key, value in {'A4B_CAPABILITY_MODE': 'off', 'A4B_CONDITION_MODE': 'off', 'A4B_REQUIREMENT_MODE': 'off',
                       'A4B_VERIFIER_MODE': 'off'}.items():
        monkeypatch.setenv(key, value)
    directory = tempfile.mkdtemp()
    return directory, handle({'directory': directory, 'action': 'new_session', 'model': 'm', 'credential_id': 'local'})


def test_attaching_to_a_new_conversation_says_nothing_and_calls_no_planner(monkeypatch):
    directory, s = _session(monkeypatch)
    calls = []
    s = handle({'directory': directory, 'action': 'upload', 'id': s['id'], 'revision': s['revision'],
                'asset': dict(DOC), 'rows': []}, conversation=lambda st: calls.append(st))
    assert s['status'] == 'empty' and s['messages'] == [] and not s['issues'] and calls == []


def test_attaching_while_a_question_waits_adds_no_assistant_text_or_issue(monkeypatch):
    directory, s = _session(monkeypatch)
    calls = []

    def planner(state):
        calls.append(state['messages'][-1]['content'])
        return {'draft': question_draft('분석할 계약서를 첨부해 주세요.'), 'confirmed': False,
                'repair_count': state.get('repair_count', 0) + 1}

    s = handle({'directory': directory, 'action': 'message', 'id': s['id'], 'revision': s['revision'],
                'content': '계약서 위험 조항을 정리해줘'}, conversation=planner)
    before = [m['content'] for m in s['messages'] if m['role'] == 'assistant']
    s = handle({'directory': directory, 'action': 'upload', 'id': s['id'], 'revision': s['revision'],
                'asset': dict(DOC), 'rows': []}, conversation=planner)
    assert [m['content'] for m in s['messages'] if m['role'] == 'assistant'] == before
    assert not s['issues'] and len(calls) == 1
    assert [r['name'] for r in s['resources']] == ['계약서.txt']


def test_attaching_clears_validation_errors_reached_without_the_file(monkeypatch):
    # PR #99 review: an earlier "file missing" validation error stayed on screen after attaching.
    directory, s = _session(monkeypatch)
    calls = []

    def planner(state):
        calls.append(state['messages'][-1]['content'])
        from tests.design.test_runtime_file_questions import question_draft
        d = question_draft('unused')
        d.update(questions=[], workflow=None, tool_ids=[], requirements=[])
        return {'draft': d, 'confirmed': False, 'repair_count': state.get('repair_count', 0) + 1}

    s = handle({'directory': directory, 'action': 'message', 'id': s['id'], 'revision': s['revision'],
                'content': '계약서 위험 조항을 정리해줘'}, conversation=planner)
    assert s['issues'], 'the fixture must start from a validation error'
    s = handle({'directory': directory, 'action': 'upload', 'id': s['id'], 'revision': s['revision'],
                'asset': dict(DOC), 'rows': []}, conversation=planner)
    assert s['issues'] == [] and s['status'] == 'needs_clarification' and len(calls) >= 1
    assert not [m for m in s['messages'] if m['role'] == 'assistant' and '자료 연결이 변경' in m['content']]
