"""Worker credentials stay in the execution context, including library source runs."""

from service import dispatch
from infrastructure.llm.errors import public_error
from workflow_engine.runner import run_workflow
from workflow_engine.unified_tools.expansion.slack_adapters import SlackDeliveryUnknown
import pytest
from workflow_engine.scheduling.library_sources import LibrarySources, SnapshotStorage


def test_dispatch_keeps_gmail_and_slack_connectors_in_snapshot_view(monkeypatch, tmp_path):
    class Storage:
        def __init__(self, directory):
            self.directory = directory
            self.db = object()

        def close(self):
            pass

    def snapshot_view(self, data):
        view = object.__new__(SnapshotStorage)
        view.storage = self.storage
        return view

    def inspect(context):
        connectors = context.storage.workflow_connectors
        assert set(connectors) == {"gmail", "slack"}
        assert callable(connectors["gmail"])
        assert callable(connectors["slack"])
        assert isinstance(context.storage, SnapshotStorage)
        return {"connected_services": sorted(connectors)}

    monkeypatch.setattr(dispatch, "Storage", Storage)
    monkeypatch.setattr(dispatch, "RunLifecycle", lambda storage: object())
    monkeypatch.setattr(dispatch, "public_result", lambda value: value)
    monkeypatch.setattr(LibrarySources, "execution_storage", snapshot_view)
    monkeypatch.setitem(dispatch.ACTIONS, "test", inspect)
    result = dispatch.handle({
        "action": "test",
        "id": "fixture-agent",
        "directory": str(tmp_path),
        "gmail_access_token": "private-gmail",
        "slack_access_token": "private-slack",
        "library_source_snapshot": {},
    })
    assert result == {"connected_services": ["gmail", "slack"]}
    assert "private-" not in repr(result)


def test_dispatch_never_attaches_service_tokens_to_design_context(monkeypatch, tmp_path):
    class Storage:
        def __init__(self, directory):
            pass

        def close(self):
            pass

    def inspect(context):
        assert not hasattr(context.storage, "workflow_connectors")
        return {"ok": True}

    monkeypatch.setattr(dispatch, "Storage", Storage)
    monkeypatch.setattr(dispatch, "RunLifecycle", lambda storage: object())
    monkeypatch.setattr(dispatch, "public_result", lambda value: value)
    monkeypatch.setitem(dispatch.ACTIONS, "test_design_token_isolation", inspect)
    assert dispatch.handle({
        "action": "test_design_token_isolation",
        "directory": str(tmp_path),
        "slack_access_token": "untrusted-client-token",
    }) == {"ok": True}


def test_worker_error_text_redacts_service_tokens():
    error = ValueError("Slack returned private-slack and Gmail returned private-gmail")
    result = public_error(error, "", "private-gmail", "private-slack")
    assert "private-slack" not in result
    assert "private-gmail" not in result


def test_unknown_slack_delivery_requires_channel_check_before_retry():
    assert "채널" in public_error(SlackDeliveryUnknown("private response"))

    class Storage:
        def test(self, run_id):
            return {"agent_id": "agent", "status": "failed", "error_type": "SlackDeliveryUnknown"}

    with pytest.raises(ValueError, match="Slack 메시지가 전송됐을 수"):
        run_workflow(Storage(), "agent", "", "", run_id="run", retry=True)


def test_interrupted_slack_run_cannot_blindly_retry():
    class Storage:
        def test(self, run_id):
            return {"agent_id": "agent", "status": "failed", "error_type": "InterruptedExecution",
                    "config_snapshot": {"workflow": {"nodes": [{"id": "send", "tool": "slack.post_message"}]}}}

    with pytest.raises(ValueError, match="Slack 메시지가 전송됐을 수"):
        run_workflow(Storage(), "agent", "", "", run_id="run", retry=True)
