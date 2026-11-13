"""Slack delivery contracts and a checkpointed Gmail-to-Slack workflow (offline)."""
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.catalog.canonical import BUSINESS_IDS, TOOL_IDS
from workflow_engine.catalog.registry import EXECUTORS, PORTS, TOOLS, node_ports
from workflow_engine.runner import run_workflow
from workflow_engine.scheduling.schedules import required_connections
from workflow_engine.unified_tools.expansion.slack_adapters import SlackAdapter, SlackDeliveryUnknown, SlackJsonTransport
from workflow_engine.workflow import workflow_errors


CHANNEL = "C0CH769U6BX"
TOKEN = "xoxb-test-secret-not-for-logs"


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode())


def test_slack_tool_is_registered_outside_the_fixed_56_business_ids():
    tool = "slack.post_message"
    assert len(BUSINESS_IDS) == 56 and tool not in BUSINESS_IDS
    assert tool in TOOL_IDS and tool in EXECUTORS and tool in PORTS
    assert next(item for item in TOOLS if item["id"] == tool)["business_tool"] is False
    assert node_ports(node("post", tool, operation="execute"))[0] == {"channel_id": "json", "text": "text"}


def test_slack_transport_posts_only_bound_text_and_returns_provider_receipt():
    opener = FakeOpener({"ok": True, "channel": CHANNEL, "ts": "1731477600.000001", "message": {"text": "do not echo"}})
    result = SlackAdapter(SlackJsonTransport(lambda: TOKEN, opener=opener))(
        "slack.post_message", {"channel_id": CHANNEL, "text": "검증한 메일 요약"})
    request, timeout = opener.requests[0]
    assert request.full_url == "https://slack.com/api/chat.postMessage"
    assert request.get_method() == "POST" and timeout == 12
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert json.loads(request.data) == {"channel": CHANNEL, "text": "검증한 메일 요약", "mrkdwn": False,
                                        "parse": "none", "unfurl_links": False, "unfurl_media": False}
    assert result == {"status": "accepted_by_provider", "channel_id": CHANNEL, "ts": "1731477600.000001"}
    assert TOKEN not in json.dumps(result) and "do not echo" not in json.dumps(result)


@pytest.mark.parametrize("channel,text", [("#general", "메시지"), ("C0CH769U6BX", ""), ("C0CH769U6BX", "\x00")])
def test_slack_rejects_unusable_inputs_before_network(channel, text):
    opener = FakeOpener()
    with pytest.raises(ValueError):
        SlackJsonTransport(lambda: TOKEN, opener=opener).post_message(channel, text)
    assert opener.requests == []


@pytest.mark.parametrize("response,expected", [
    ({"ok": False, "error": "not_in_channel"}, "초대"),
    ({"ok": False, "error": "invalid_auth"}, "다시 연결"),
    ({"ok": False, "error": [TOKEN]}, "보내지 못했습니다"),
])
def test_slack_provider_errors_are_actionable_and_never_expose_token(response, expected):
    with pytest.raises(RuntimeError) as failure:
        SlackJsonTransport(lambda: TOKEN, opener=FakeOpener(response)).post_message(CHANNEL, "요약")
    assert expected in str(failure.value) and TOKEN not in str(failure.value)


@pytest.mark.parametrize("error", [
    URLError(TOKEN),
    HTTPError("https://slack.com/api/chat.postMessage", 401, TOKEN, {}, None),
])
def test_slack_transport_scrubs_network_and_auth_exception_details(error):
    with pytest.raises(RuntimeError) as failure:
        SlackJsonTransport(lambda: TOKEN, opener=FakeOpener(error=error)).post_message(CHANNEL, "요약")
    assert TOKEN not in str(failure.value)
    if type(error) is URLError:
        assert isinstance(failure.value, SlackDeliveryUnknown)


def gmail_to_slack_workflow():
    mail = node("mail", "email.list", operation="read_messages", arguments_json='{"max_results":1}')
    summary = node("summary", "compose_answer", [bind("documents", "mail", "documents")], instructions="조회된 메일을 요약하세요.")
    post = node("post", "slack.post_message", [bind("text", "summary", "text")], operation="execute",
                arguments_json=json.dumps({"channel_id": CHANNEL}))
    return {"entry": "mail", "nodes": [mail, summary, post], "edges": [edge("mail", "summary"), edge("summary", "post")]}


def test_gmail_summary_flows_into_slack_once_without_saving_token(tmp_path):
    workflow = gmail_to_slack_workflow()
    assert workflow_errors(workflow, ["email.list", "compose_answer", "slack.post_message"], []) == []
    assert required_connections({"workflow": workflow}) == ["gmail", "slack"]
    store, agent_id, _ = saved(tmp_path, workflow)
    calls = []
    try:
        def gmail(operation, args):
            calls.append((operation, args))
            return {"messages": []}

        def slack(operation, args):
            calls.append((operation, args))
            return {"status": "accepted_by_provider", "channel_id": args["channel_id"], "ts": "1731477600.000001"}

        store.workflow_connectors = {"gmail": gmail, "slack": slack}
        run = run_workflow(store, agent_id, "어제 메일 요약", "unused", llm=object())
        assert run["status"] == "succeeded", run.get("error")
        assert calls[0][0] == "email.list"
        assert calls[1] == ("slack.post_message", {"channel_id": CHANNEL, "text": run["outputs"]["summary"]["text"]})
        assert run["outputs"]["post"]["result"]["status"] == "accepted_by_provider"
        assert TOKEN not in json.dumps(run, ensure_ascii=False)
    finally:
        store.close()


@pytest.mark.parametrize("change", [
    lambda step: step.update(bindings=[]),
    lambda step: step["settings"].update(arguments_json=json.dumps({"channel_id": CHANNEL, "text": "임의 텍스트"})),
    lambda step: step["settings"].update(arguments_json='{"channel_id":"#general"}'),
])
def test_slack_requires_real_channel_and_predecessor_text_binding(change):
    workflow = gmail_to_slack_workflow()
    change(workflow["nodes"][-1])
    assert workflow_errors(workflow, ["email.list", "compose_answer", "slack.post_message"], [])


def test_saved_child_workflow_reports_slack_connection():
    child = {"workflow": gmail_to_slack_workflow()}
    parent = {"workflow": {"nodes": [{"id": "child", "tool": "saved_workflow"}]}}
    assert required_connections(parent, {"child": {"config": child, "workflow_dependencies": {}}}) == ["gmail", "slack"]
