from agent_builder.nodes.conversation import inspection_mistaken_for_draft


def test_internal_inspection_with_attachment_retries_once_without_inventing_plan():
    draft={'name':'inspect_resource','workflow':None,'questions':['자료를 확인할 수 있나요?'],'unsupported':[]}
    assert inspection_mistaken_for_draft(draft,{'doc':{}},[{'role':'user','content':'첨부한 정책으로 검수해 줘'}])


def test_user_named_workflow_and_legitimate_question_are_not_rewritten():
    draft={'name':'inspect_resource','workflow':None,'questions':['어떤 기준인가요?'],'unsupported':[]}
    assert not inspection_mistaken_for_draft(draft,{'doc':{}},[{'role':'user','content':'이름은 inspect_resource'}])
    assert not inspection_mistaken_for_draft({**draft,'name':'환불 검수'}, {'doc':{}}, [{'role':'user','content':'검수'}])
    assert not inspection_mistaken_for_draft(draft,{},[{'role':'user','content':'검수'}])
