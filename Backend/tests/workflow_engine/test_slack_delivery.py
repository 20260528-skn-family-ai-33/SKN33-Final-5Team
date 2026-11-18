"""Slack delivery contracts and a checkpointed Gmail-to-Slack workflow (offline)."""
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import httpx
import pytest

from infrastructure.llm.model_factory import create_chat_model
from tests.workflow_engine.test_gmail_batch_read import message
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


@pytest.mark.parametrize('truncations', [0, 1, 2])
def test_gmail_verification_expands_budget_without_replaying_reads_or_sending_partial_summary(
    tmp_path, monkeypatch, truncations,
):
    from workflow_engine.executors import agent_factory

    workflow = gmail_to_slack_workflow()
    workflow['nodes'][0]['settings']['arguments_json'] = '{"max_results":5}'
    ids = [f'm{index}' for index in range(1, 6)]
    gmail_calls, slack_calls, drafts, verification_requests = [], [], [], []
    final_summary = '\n'.join(f'제목 {identifier}: 핵심 일정 확인' for identifier in ids)

    def gmail(operation, args):
        gmail_calls.append(operation)
        if operation == 'email.list':
            return {'messages': [{'id': identifier} for identifier in ids]}
        return message(args['message_id'], '핵심 일정 확인')

    def slack(operation, args):
        slack_calls.append((operation, args))
        return {'status': 'accepted_by_provider', 'channel_id': CHANNEL, 'ts': '1731477600.000001'}

    def grounded_agent(**options):
        evidence = options['tools'][0].invoke({})
        draft = {'text': '초안', 'citations': [{'evidence_id': item['evidence_id']} for item in evidence]}
        drafts.append(draft)
        return SimpleNamespace(invoke=lambda *args: {'structured_response': draft})

    def verification_response(request):
        verification_requests.append(json.loads(request.content))
        truncated = len(verification_requests) <= truncations
        text = '{"text":' if truncated else json.dumps({**drafts[0], 'text': final_summary})
        chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fixture',
                 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': text},
                              'finish_reason': 'length' if truncated else 'stop'}]}
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                              text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')

    monkeypatch.setattr(agent_factory, 'create_deep_agent', grounded_agent)
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://fixture.test/v1')
    monkeypatch.setenv('A4B_OLLAMA_NUM_PREDICT', '8192')
    monkeypatch.delenv('A4B_MAIN_API_KEY', raising=False)
    store, agent_id, _ = saved(tmp_path, workflow)
    try:
        store.workflow_connectors = {'gmail': gmail, 'slack': slack}
        with httpx.Client(transport=httpx.MockTransport(verification_response)) as client:
            model = create_chat_model('', 'fixture', max_tokens=5000, max_retries=0, http_client=client)
            run = run_workflow(store, agent_id, '안 읽은 Gmail 5건을 요약해 Slack에 보내줘', '', llm=model)
    finally:
        store.close()

    assert gmail_calls == ['email.list'] + ['email.get'] * 5
    assert len(drafts) == 1
    assert [payload['max_tokens'] for payload in verification_requests] == ([5000, 8192] if truncations else [5000])
    if truncations:
        initial, retry = verification_requests
        assert retry['messages'] == initial['messages']
        assert retry['response_format'] == initial['response_format']
    if truncations == 2:
        assert run['status'] == 'failed' and run['error_type'] == 'LengthFinishReasonError'
        assert run['steps'][-1]['node'] == 'summary'
        assert not slack_calls
    else:
        assert run['status'] == 'succeeded', run.get('error')
        summary = run['outputs']['summary']
        assert {item['source_id'] for item in summary['evidence']} == {'gmail:' + identifier for identifier in ids}
        assert slack_calls == [('slack.post_message', {'channel_id': CHANNEL, 'text': final_summary})]


@pytest.mark.parametrize('read_count,summary_count', [(5, 3), (2, 2), (0, 0)])
def test_gmail_slack_reports_actual_counts_and_preserves_partial_summary(tmp_path, monkeypatch, read_count, summary_count):
    from agent_builder.design.gmail_slack import SUMMARY_CARD_FORMAT
    from workflow_engine.executors import agent_factory

    workflow = gmail_to_slack_workflow()
    workflow['nodes'][0]['settings']['arguments_json'] = '{"max_results":5,"query":"is:unread"}'
    workflow['nodes'][1]['settings']['instructions'] += SUMMARY_CARD_FORMAT
    ids = [f'm{index}' for index in range(read_count, 0, -1)]
    shown = ids[::2] if read_count == 5 else ids[:summary_count]
    gmail_calls, slack_calls, drafts, verification_requests = [], [], [], []

    def cards(identifiers):
        return '\n\n'.join(
            f'제목: 제목 {identifier}\n보낸 사람: sender@example.com\n핵심: 확인할 업무 {identifier}'
            for identifier in identifiers
        )

    def gmail(operation, args):
        gmail_calls.append((operation, args))
        if operation == 'email.list':
            listing = {'messages': [{'id': identifier} for identifier in ids]}
            if read_count == 5:
                listing['nextPageToken'] = 'next'
            return listing
        identifier = args['message_id']
        return message(identifier, f'확인할 업무 {identifier}' if identifier in shown else '행사 홍보 안내')

    def slack(operation, args):
        slack_calls.append((operation, args))
        return {'status': 'accepted_by_provider', 'channel_id': CHANNEL, 'ts': '1731477600.000001'}

    def grounded_agent(**options):
        evidence = options['tools'][0].invoke({})
        draft = {'text': cards(ids), 'citations': [{'evidence_id': item['evidence_id']} for item in evidence]}
        drafts.append(draft)
        return SimpleNamespace(invoke=lambda *args: {'structured_response': draft})

    def verify(messages):
        verification_requests.append(json.loads(messages[-1]['content']))
        return {'text': cards(shown), 'citations': []}

    def with_structured_output(schema, *, method, strict):
        assert method == 'json_schema' and strict
        return SimpleNamespace(invoke=verify)

    monkeypatch.setattr(agent_factory, 'create_deep_agent', grounded_agent)
    store, agent_id, _ = saved(tmp_path, workflow)
    try:
        store.workflow_connectors = {'gmail': gmail, 'slack': slack}
        llm = SimpleNamespace(with_structured_output=with_structured_output)
        run = run_workflow(store, agent_id, '안 읽은 Gmail 5건을 요약해 Slack에 보내줘', '', llm=llm)
    finally:
        store.close()

    assert gmail_calls == [('email.list', {'max_results': 5, 'query': 'is:unread'})] + [
        ('email.get', {'message_id': identifier, 'format': 'full'}) for identifier in ids
    ]
    assert run['status'] == 'succeeded', run.get('error')
    text = run['outputs']['summary']['text']
    counts = f'메일 처리 결과: 읽음 {read_count}건 · 요약 {summary_count}건 · 누락 {read_count - summary_count}건'
    assert counts in text
    assert text in run['answer']
    assert slack_calls == [('slack.post_message', {'channel_id': CHANNEL, 'text': text})]
    assert '광고' not in text and '홍보' not in text
    notice = '검색 결과가 더 있어 조회 한도 밖의 메일은 이번 조회에서 제외했습니다.'
    assert (notice in text) == (read_count == 5)
    if ids:
        assert len(drafts) == len(verification_requests) == 1
        assert verification_requests[0]['draft'] == drafts[0]
        assert cards(shown) in text
        assert all(f'제목: 제목 {identifier}' not in text for identifier in ids if identifier not in shown)
    else:
        assert not drafts and not verification_requests


def test_gmail_slack_counts_distinct_matches_and_ignores_cards_without_a_summary():
    from workflow_engine.executors.document.gmail_slack_summary import summarized_mail_ids

    header = '제목: 같은 제목\n보낸 사람: 보낸 이 <sender@example.com>'
    documents = [
        {'source_id': 'gmail:first', 'file_name': '같은 제목', 'text': header + '\n\n첫 번째 업무'},
        {'source_id': 'gmail:second', 'file_name': '같은 제목', 'text': header + '\n\n두 번째 업무'},
    ]
    first_card = header + '\n핵심: 첫 번째 업무'
    duplicate_card = header + '\n핵심: 첫 번째 업무\ncitations=["e1"]'
    empty_card = header + '\n핵심:\ncitations=["e2"]'
    unmatched = header.replace('같은 제목', '다른 제목') + '\n핵심: 확인하지 않은 업무'

    text = '\n\n'.join([first_card, duplicate_card, empty_card, unmatched])
    assert summarized_mail_ids(text, documents) == {'gmail:first'}
    text += '\n\n**제목:** 같은 제목\n**보낸 사람:** sender@example.com\n**핵심:** 두 번째 업무'
    assert summarized_mail_ids(text, documents) == {'gmail:first', 'gmail:second'}


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
