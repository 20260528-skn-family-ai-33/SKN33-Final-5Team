from copy import deepcopy

from workflow_engine import legacy_runtime


def test_legacy_model_failure_persists_terminal_status(monkeypatch):
    history = []

    class Storage:
        def agent(self, agent_id):
            return {'version': 1, 'model': 'fixture', 'config': {'document_ids': []}}

        def save_test(self, run):
            history.append(deepcopy(run))

    def unavailable(**kwargs):
        raise TimeoutError('fixture timeout')

    monkeypatch.setattr(legacy_runtime, 'create_chat_model', unavailable)
    run = legacy_runtime.test_agent(Storage(), 'fixture', '실행', '')
    assert [snapshot['status'] for snapshot in history] == ['running', 'failed']
    assert run['error_type'] == 'TimeoutError'
