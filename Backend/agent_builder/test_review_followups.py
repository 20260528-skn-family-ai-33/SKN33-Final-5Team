from agent_builder.workflow import workflow_errors

SETTINGS={'file_ids':['t'],'query':None,'fields':[],'instructions':None,'format':None,'question':None,
          'condition_field':None,'condition_operator':None}
RESOURCES=[{'id':'t','kind':'table','columns':['stock']}]


def plan(**changes):
    return {'filters':[],'sort':[],'match':'all','missing_policy':'ask','duplicate_policy':'keep',
            'duplicate_field':None,'output_format':'csv',**changes}


def read_workflow(execution_plan):
    node={'id':'read','tool':'read_table','settings':{**SETTINGS,'execution_plan':execution_plan},'bindings':[]}
    return {'entry':'read','nodes':[node],'edges':[]}


def test_filter_on_a_read_node_is_rejected_because_it_would_be_ignored():
    p=plan(filters=[{'field':'stock','operator':'gte','value':3,'compare_field':None}])
    assert any('filter_sort 노드에만' in i for i in workflow_errors(read_workflow(p),['read_table'],RESOURCES))


def test_missing_and_duplicate_policies_on_a_read_node_are_rejected():
    for changes in ({'missing_policy':'exclude'},{'duplicate_policy':'first','duplicate_field':'stock'}):
        assert any('filter_sort 노드에만' in i for i in workflow_errors(read_workflow(plan(**changes)),['read_table'],RESOURCES))


def test_neutral_plan_on_a_read_node_is_still_accepted():
    assert workflow_errors(read_workflow(plan()),['read_table'],RESOURCES)==[]


def _seeded(tmp_path):
    from agent_builder.storage import Storage
    from agent_builder.test_verifier_bridge import seed
    return seed(Storage(tmp_path))


def test_global_filter_without_a_filter_node_is_rejected(tmp_path):
    from copy import deepcopy
    from agent_builder.nodes.validation import validate_node
    from agent_builder.test_workflow import bind, edge, node
    state,raw=_seeded(tmp_path)
    draft=deepcopy(raw)
    read=draft['workflow']['nodes'][0]
    draft['workflow']={'entry':'read','nodes':[read,node('save','write_result',[bind('table','read','table')],format='json')],
                       'edges':[edge('read','save')]}
    draft['tool_ids']=['read_table','write_result']
    result=validate_node({**state,'draft':draft})
    assert any('filter_sort 노드가 워크플로우에 필요' in i for i in result['issues'])


def test_first_edit_compares_against_the_saved_draft(tmp_path):
    from copy import deepcopy
    from agent_builder.nodes.verification import make_verification_node
    from agent_builder.storage import Storage
    store=Storage(tmp_path)
    state,raw=_seeded(tmp_path)
    store=Storage(tmp_path)
    seen={}
    def recording_verifier(data):
        from agent_builder.test_verifier_bridge import fixture_verifier
        seen['input']=data
        return fixture_verifier(data)
    edited=deepcopy(raw);edited['purpose']=edited['purpose']+' (수정)'
    state={**state,'messages':[dict(role='user',content='보증금 100만원 이상만 JSON으로 저장해줘.'),
                               dict(role='assistant',content='저장했습니다.'),
                               dict(role='user',content='정렬은 유지하고 금액만 바꿔줘.')],
           'draft':edited,'verification_last_candidate':deepcopy(raw)}
    make_verification_node(store,verifier=recording_verifier,mode='shadow')(state)
    assert seen['input'].previous_draft is not None
    assert seen['input'].latest_user_answer_id is not None
