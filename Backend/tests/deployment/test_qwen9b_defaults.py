"""Exercise delivered 9B settings without network, GPU or production storage."""
from copy import deepcopy
from pathlib import Path

import pytest

from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.verification import make_verification_node
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest, validate_plan
from infrastructure.persistence.storage import Storage
from tests.design.test_deep_agent_conversation import draft, state
from tests.verification.test_verifier_bridge import seed, fixture_verifier
from workflow_engine.runner import run_workflow


ROOT = Path(__file__).resolve().parents[3]


def checked_seed(store):
    """Include the requirement ledger normally produced before design validation."""
    original, raw = seed(store)
    request = original['messages'][0]['content']
    raw['instructions'] = request
    raw['purpose'] = request
    tasks = [
        dict(id='r1', source_message=0, quote=request, description='보증금 조건 선별',
             depends_on=[], spec=dict(kind='filter', filters=deepcopy(raw['execution_plan']['filters']), match='all')),
        dict(id='r2', source_message=0, quote=request, description='JSON 파일 저장',
             depends_on=['r1'], spec=dict(kind='export', format='json')),
    ]
    ledger = validate_plan(dict(tasks=tasks, unresolved=[]), original)
    original.update(requirement_required=True, requirement_ledger=ledger,
                    requirement_fingerprint=ledger_fingerprint(original), requirement_digest=plan_digest(ledger))
    return original, raw


@pytest.fixture(params=['.env.aws.qwen35-9b.example', '.env.ec2.qwen35-9b.example'])
def profile(monkeypatch, request):
    # An operator applies this overlay to an existing direct/shadow deployment.
    monkeypatch.setenv('A4B_PLANNER_MODE', 'direct')
    monkeypatch.setenv('A4B_VERIFIER_MODE', 'shadow')
    # These versioned fixtures use literal assignments only; actual dotenv/Compose
    # parsing and overlay precedence are exercised by deployment-model-env.test.ts.
    profiles = [request.param]
    if request.param == '.env.aws.qwen35-9b.example':
        profiles.append('.env.aws.qwen35-9b-deepagent.example')
    for name in profiles:
        for line in (ROOT / 'infrastructure/env' / name).read_text(encoding='utf-8').splitlines():
            if not line or line.startswith('#'):
                continue
            key, value = line.split('=', 1)
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            monkeypatch.setenv(key, value)
    return request.param


def test_9b_profile_uses_deepagent_instead_of_direct(monkeypatch, profile):
    factories = []

    def create(**kwargs):
        factories.append(kwargs)

        class Agent:
            def invoke(self, payload, config):
                return {'structured_response': draft(questions=['집계 기준을 알려주세요.'])}

        return Agent()

    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', create)
    monkeypatch.setattr('agent_builder.design.nodes.direct_conversation.make_direct_conversation',
                        lambda *a, **kw: pytest.fail('The 9B profile must not select direct'))
    result = make_conversation(None, 'a4b-qwen35-9b-final664-pilot32k',
                               model_factory=lambda: object())(
                                   state([{'role': 'user', 'content': '집계 기준을 확인해줘'}]))
    assert len(factories) == 1
    assert result['draft']['questions'] == ['집계 기준을 알려주세요.']
    assert 'Deep Agents' in result['trace'][-1]['detail']


def test_reviewer_off_keeps_native_validation_approval_and_real_execution(tmp_path, profile):
    store = Storage(tmp_path)
    try:
        original, raw = checked_seed(store)
        original.update(draft=raw, verification={'route': 'manual_review'},
                        verification_route='manual_review', verification_required=False,
                        verification_enforced=False)
        disabled = make_verification_node(store, verifier=lambda _: pytest.fail('No 4B reviewer call'))(original)
        assert disabled['verification_route'] is None
        assert not disabled['verification_enforced']
        assert store.db.execute("SELECT name FROM sqlite_master WHERE name='builder_verifications'").fetchone() is None
        checked = validate_node({**original, **disabled})
        assert checked['status'] == 'ready'
        with pytest.raises(ValueError):
            make_save_node(store)({**original, **disabled, **checked, 'confirmed': False})
        saved = make_save_node(store)({**original, **disabled, **checked, 'confirmed': True})
        run = run_workflow(store, saved['saved_agent_id'], '저장한 조건으로 실행', api_key='')
        assert run['status'] == 'succeeded'
        assert [row['번호'] for row in run['rows']] == ['B', 'C']
        assert run['report_path']
        assert (tmp_path / run['report_path']).is_file()
    finally:
        store.close()


def test_reviewer_off_does_not_allow_an_invalid_workflow_to_be_saved(tmp_path, profile):
    store = Storage(tmp_path)
    try:
        original, raw = checked_seed(store)
        invalid = deepcopy(raw)
        invalid['workflow']['nodes'][1]['bindings'][0]['node_id'] = 'missing-source'
        original['draft'] = invalid
        disabled = make_verification_node(store, verifier=lambda _: pytest.fail('No 4B reviewer call'))(original)
        checked = validate_node({**original, **disabled})
        assert checked['status'] != 'ready'
        assert checked['issues'], 'Native validation must report the broken data binding'
        with pytest.raises(ValueError):
            make_save_node(store)({**original, **disabled, **checked, 'confirmed': True})
        assert not store.agents()
    finally:
        store.close()


@pytest.mark.parametrize('flag', ['verification_required', 'verification_enforced'])
def test_reviewer_off_overrides_legacy_flags_and_preserves_history(tmp_path, profile, flag):
    store = Storage(tmp_path)
    try:
        original, raw = checked_seed(store)
        original.update(draft=raw, **{flag: True})
        history = make_verification_node(store, verifier=fixture_verifier, mode='enforce')(original)
        original.update(history)
        before = deepcopy(original)
        records = store.db.execute('SELECT * FROM builder_verifications').fetchall()
        result = make_verification_node(store, verifier=lambda _: pytest.fail('No 4B reviewer call'))(original)
        assert not result['verification_required']
        assert not result['verification_enforced']
        assert result['verification_route'] is None
        assert original == before
        assert {**original, **result}['verification'] == history['verification']
        assert store.db.execute('SELECT * FROM builder_verifications').fetchall() == records
    finally:
        store.close()
