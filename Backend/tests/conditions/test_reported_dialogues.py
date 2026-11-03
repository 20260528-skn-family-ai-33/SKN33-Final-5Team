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


def test_yes_to_column_question_reasks_without_model_unsupported():
    s=state('중복된 행을 첫 행만 남겨줘')
    first=validate_conditions(proposal(s,{'duplicate.policy':'first','duplicate.field':None},['deduplicate']),s,source_catalog(s)[0])
    s.update(condition_ledger=first,pending_questions=first['questions'])
    s['messages'] += [{'role':'assistant','content':'어느 열의 값이 같으면 중복으로 볼까요?'},{'role':'user','content':'네'}]
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
