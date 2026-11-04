import pytest
from agent_builder.conditions.condition_check import make_condition_check
from agent_builder.conditions.condition_readiness import judge_conditions,validate_conditions,source_catalog
from tests.conditions.test_condition_readiness import state,proposal


def test_sales_comparison_never_enters_deduplication_model():
    s=state('10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해줘. 반품을 뺀 매출로 보고 정정 내역도 반영해줘. 지점별 증감과 매장·온라인 변화, 회의용 표와 짧은 요약을 만들어줘.')
    class Forbidden:
        def invoke(self,*a):pytest.fail('표 전용 조건 모델 호출')
    result=make_condition_check(transport=Forbidden(),mode='enforce')(s)
    assert result['condition_route']=='conversation' and result['condition_ledger'] is None


def test_keep_does_not_require_duplicate_key():
    s=state('중복된 행은 모두 유지해')
    result=validate_conditions(proposal(s,{'duplicate.policy':'keep','duplicate.field':None},['deduplicate']),s,source_catalog(s)[0])
    assert 'duplicate.field' not in result['unresolved']
    assert 'deduplicate' not in result['operations']


@pytest.mark.parametrize('reply',['네','네.',' 예! ','알겠습니다.'])
def test_yes_to_column_question_reasks_without_model_unsupported(reply):
    s=state('중복된 행을 첫 행만 남겨줘')
    first=validate_conditions(proposal(s,{'duplicate.policy':'first','duplicate.field':None},['deduplicate']),s,source_catalog(s)[0])
    s.update(condition_ledger=first,pending_questions=first['questions'])
    s['messages'] += [{'role':'assistant','content':'어느 열의 값이 같으면 중복으로 볼까요?'},{'role':'user','content':reply}]
    result=judge_conditions(s,None)['ledger']
    assert result['decision']=='clarify' and not result['unsupported_actions']
    assert any(q['key']=='duplicate.field' for q in result['questions'])


@pytest.mark.parametrize('text',['영수증 올려줄테니까 엑셀로 정리해줘','파일 올려줄게 정리해줘','파일 올릴테니 확인해줘'])
def test_imminent_upload_prompts_without_model(text):
    from agent_builder.design.attachment_precheck import missing_attachment_reply
    assert missing_attachment_reply(state(text))['draft']['questions']


def test_uploaded_receipt_recovers_original_table_intent():
    from agent_builder.design.document_rows_template import document_rows_applies,document_rows_reply
    s=state('영수증을 엑셀로 정리해줘')
    s['messages'] += [{'role':'assistant','content':'파일을 올려주세요'},{'role':'user','content':'영수증 파일 올려놨어'}]
    s['resources']=[{'id':'receipt','kind':'document','name':'receipt.png'}]
    assert document_rows_applies(s)
    assert document_rows_reply(s)['draft']['questions']


def test_reasoning_explanation_is_not_rendered_but_question_is_preserved():
    from agent_builder.design.presentation import public_draft_explanation
    original={'explanation':'따라서 사용자에게 명확한 질문을 해야 합니다.','questions':['정리할 영수증 파일을 업로드해 주세요.'],'unsupported':[]}
    result=public_draft_explanation(original)
    assert '따라서' not in result['explanation']
    assert result['questions']==original['questions']
    assert original['explanation'].startswith('따라서')


def test_capability_file_id_is_an_upload_instruction_not_internal_noun():
    from agent_builder.capabilities.capability_check import _question_text
    assert _question_text('파일 ID')=='처리할 파일을 자료 연결에서 업로드해 주세요.'
    assert _question_text('비교할 기간')=='비교할 기간에 대해 알려주세요.'


@pytest.mark.parametrize(('condition', 'prompt'), [
    ('파일 형식을 확인해야 합니다.', '파일 형식을 확인해 주세요.'),
    ('필요한 항목을 정의해야 합니다', '필요한 항목을 정의해 주세요.'),
    ('자료 연결이 필요합니다.', '자료 연결이 필요합니다.'),
    ('어떤 형식인가요?', '어떤 형식인가요?'),
])
def test_complete_condition_sentence_is_not_treated_as_a_noun(condition, prompt):
    from agent_builder.capabilities.capability_check import _question_text
    assert _question_text(condition) == prompt


def test_keep_only_is_ready_without_empty_review():
    s=state('중복된 행은 모두 유지해')
    r=validate_conditions(proposal(s,{'duplicate.policy':'keep','duplicate.field':None},['deduplicate']),s,source_catalog(s)[0])
    assert r['decision']=='ready' and not r['questions']


def test_short_unrecognised_reasoning_is_never_public():
    from agent_builder.design.presentation import public_draft_explanation
    text='먼저 사용자의 의도를 분석하고 필요한 정보를 파악해야 한다.'
    assert text not in public_draft_explanation({'explanation':text,'questions':[]})['explanation']


def test_upload_precedes_enforced_capability_model(tmp_path):
    from infrastructure.persistence.storage import Storage
    from agent_builder.design.graph import build_graph
    from tests.design.test_builder import initial
    class Forbidden:
        def invoke(self,*a):pytest.fail('파일 안내 전에 모델 호출')
    store=Storage(tmp_path);s=initial();s['messages']=[{'role':'user','content':'영수증 올려줄테니까 엑셀로 정리해줘'}]
    graph,connection=build_graph(store,capability_transport=Forbidden(),capability_mode='enforce',condition_mode='enforce')
    try:
        r=graph.invoke(s,{'configurable':{'thread_id':s['session_id']}})
        assert r['status']=='needs_clarification' and r['__interrupt__'][0].value['kind']=='clarification'
        assert '파일' in r['draft']['questions'][0]
    finally:connection.close();store.close()


@pytest.mark.parametrize('user_request',['이번 달과 지난달의 실적 차이를 비교해줘','환불을 반영한 매출 요약을 만들어줘','매장별 증가율을 계산해줘'])
def test_comparison_and_summary_bypass_single_table_condition_model(user_request):
    class Forbidden:
        def invoke(self,*a):pytest.fail('범위 밖 작업을 중복 제거로 분류하면 안 됨')
    r=make_condition_check(transport=Forbidden(),mode='enforce')(state(user_request))
    assert r['condition_route']=='conversation' and r['condition_ledger'] is None


def test_planner_refusal_cannot_override_current_supported_assessment(tmp_path):
    from tests.design.test_builder import initial,draft
    from agent_builder.capabilities.capability_gate import input_fingerprint
    from agent_builder.design.nodes.validation import validate_node
    from agent_builder.design.nodes.supervisor import supervisor_node
    from agent_builder.design.nodes.save import make_save_node
    from infrastructure.persistence.storage import Storage
    s=initial()
    s.update(capability_enforced=True,capability_assessment={'decision':'existing_tools','tool_ids':['read_table'],'missing_conditions':[],'missing_actions':[]})
    s['capability_fingerprint']=input_fingerprint(s)
    s.update(draft={**draft(),'tool_ids':[],'unsupported':['사용자 입력이 없어 미지원입니다.']},repair_count=2,confirmed=True)
    checked=validate_node(s)
    assert checked['status']=='validation_failed' and any('충돌' in i for i in checked['issues'])
    assert supervisor_node({**s,**checked})['route']=='invalid'
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError):make_save_node(store)(s)
        assert not store.agents()
    finally:store.close()
