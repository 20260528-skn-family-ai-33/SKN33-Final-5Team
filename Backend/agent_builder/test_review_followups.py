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


def test_worker_edit_session_gives_the_verifier_the_saved_draft(tmp_path,monkeypatch):
    """End to end through worker.handle: the first edit must be compared with the saved agent."""
    import json
    import agent_builder.nodes.verification as verification_module
    from a4b_verifier.service import verify
    from agent_builder.storage import uid
    from agent_builder.test_builder import PROMPT, draft
    from agent_builder.test_verifier_bridge import EXAMPLES
    from agent_builder.test_workflow import bind, edge, node
    from agent_builder.worker import handle
    seen=[]
    def recording_verify(data,*args,**kwargs):
        seen.append(data)
        output=json.loads((EXAMPLES/'model_outputs/01_ready.json').read_text())
        return verify(data,mode='fixture',fixture=output)
    monkeypatch.setattr(verification_module,'verify',recording_verify)
    monkeypatch.setenv('A4B_VERIFIER_MODE','shadow')
    plan=dict(filters=[dict(field='stock',operator='lt',value=10,compare_field=None,value_type='number')],sort=[],
              match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='csv')
    def conversation(name):
        def run(state):
            rid=state['resources'][0]['id']
            wf=dict(entry='read',nodes=[node('read','read_table',file_ids=[rid]),
                    node('filter','filter_sort',[bind('table','read','table')],execution_plan=plan),
                    node('save','write_result',[bind('table','filter','table')],format='csv')],
                    edges=[edge('read','filter'),edge('filter','save')])
            return {'draft':{**draft(),'name':name,'workflow':wf,'execution_plan':plan,'document_ids':[rid]},'confirmed':False}
        return run
    directory=str(tmp_path)
    s=handle({'directory':directory,'action':'new_session','model':'gpt-4.1-mini','credential_id':uid()})
    s=handle({'directory':directory,'action':'upload','id':s['id'],'revision':s['revision'],
              'asset':{'name':'stock.csv','kind':'table','columns':['stock'],'profile':{},'row_count':2,'preview':[{'stock':1},{'stock':20}],'format':'csv','version':1,'hash':'h'},
              'rows':[{'stock':1},{'stock':20}]})
    s=handle({'directory':directory,'action':'message','id':s['id'],'revision':s['revision'],'content':PROMPT},conversation=conversation('재고 정리'))
    assert s['status']=='ready',s.get('issues')
    s=handle({'directory':directory,'action':'confirm','id':s['id'],'revision':s['revision']},conversation=conversation('재고 정리'))
    assert s['saved_agent_id']
    seen.clear()
    edit=handle({'directory':directory,'action':'new_session','model':'gpt-4.1-mini','credential_id':s['credential_id'],'agent_id':s['saved_agent_id']})
    handle({'directory':directory,'action':'message','id':edit['id'],'revision':edit['revision'],'content':'이름만 수정'},conversation=conversation('이름만 수정'))
    assert seen,'verifier was not called on the first edit'
    assert seen[-1].previous_draft is not None
    assert seen[-1].candidate_draft is not None
