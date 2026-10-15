import pytest
from types import SimpleNamespace
from agent_builder.nodes.validation import validate_node
from agent_builder.nodes.supervisor import supervisor_node
from agent_builder.test_workflow import node,bind,edge
from agent_builder.output_contracts import ensure_requested_output



def test_ready_invalid_graph_still_cannot_save():
    result=supervisor_node({'status':'needs_clarification','draft':{'questions':[]},'issues':['잘못된 포트'],'repair_count':2})
    assert result['route']=='invalid';assert result['status']=='validation_failed'


def test_output_is_appended_to_extract_terminal():
    draft={'workflow':{'entry':'read','nodes':[node('read','read_document'),node('extract','extract_information',[bind('documents','read','documents')],fields=['번호'])],'edges':[edge('read','extract')]},'tool_ids':['read_document','extract_information']}
    ensure_requested_output(draft,'json')
    assert draft['workflow']['nodes'][-1]['tool']=='export_result'
    assert draft['workflow']['nodes'][-1]['bindings']==[bind('records','extract','records')]
    ensure_requested_output(draft,'json');assert len(draft['workflow']['nodes'])==3


def test_candidate_review_never_gains_automatic_export():
    draft={'workflow':{'entry':'review','nodes':[node('review','reply_review')],'edges':[]},'tool_ids':['reply_review']}
    ensure_requested_output(draft,'json');assert len(draft['workflow']['nodes'])==1


def test_agent_node_forces_inputs_before_answer(monkeypatch):
    import deepagents
    from agent_builder.node_library import run_agent_node
    from langchain_core.messages import AIMessage,ToolMessage
    seen=[]
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']};middleware=kwargs['middleware'][0]
        class Request:
            tools=list(kwargs['tools'])
            def override(self,**changes):return SimpleNamespace(**changes)
        class Agent:
            def invoke(self,*a):
                middleware.wrap_model_call(Request(),lambda request:seen.append(request.tool_choice))
                tools['inspect_inputs'].invoke({})
                middleware.wrap_model_call(Request(),lambda request:seen.append(request.tool_choice))
                return {'messages':[ToolMessage(content='{}',name='inspect_inputs',tool_call_id='read'),AIMessage(content='실제 입력 답변')]}
        return Agent()
    monkeypatch.setattr(deepagents,'create_deep_agent',factory)
    n=node('agent','agent_node',node_ref='fixture:1')
    config={'node_snapshots':[{'ref':'fixture:1','spec':{'reference':'','instructions':'실제 입력 사용','model':None}}],'document_ids':[],'instructions':'업무'}
    result=run_agent_node(n,{'request':'질문','outputs':{},'file_ids':[]},None,config,object())
    assert result['text']=='실제 입력 답변';assert seen[0]['function']['name']=='inspect_inputs';assert seen[1] is None

@pytest.mark.parametrize('policy,expected',[('error',None),('ask',None),('exclude',[{'team':'A','count':1}]),('keep',[{'team':'A','count':1},{'team':'','count':1}])])
def test_group_policy(policy,expected):
    from agent_builder.executors.aggregate_table import execute
    n=node('g','aggregate_table',[bind('table','r','table')],fields=['team'],aggregation='count')
    state={'outputs':{'r':{'table':{'rows':[{'team':'A'},{'team':''}],'columns':['team'],'file_id':'test'}}}}
    if expected is None:
        with pytest.raises(ValueError):execute(n,state,None,{'execution_plan':{'missing_policy':policy}},None)
    else:assert execute(n,state,None,{'execution_plan':{'missing_policy':policy}},None)['table']['rows']==expected


def test_export_supports_declared_answer_port():
    from agent_builder.executors.export_result import execute
    n=node('out','export_result',[bind('answer','answer_node','text')],format='markdown')
    result=execute(n,{'outputs':{'answer_node':{'text':'확인 불가 안내'}}},None,{'workflow':{'nodes':[n],'edges':[]}},None)
    assert result['artifact']['content']=='확인 불가 안내'


def test_file_save_precedes_finish_in_display_order():
    draft={'workflow':{'entry':'read','nodes':[node('read','read_table'),node('done','finish')],'edges':[edge('read','done')]},'tool_ids':['read_table','finish']}
    ensure_requested_output(draft,'csv')
    assert [n['tool'] for n in draft['workflow']['nodes']]==['read_table','write_result','finish']




def test_only_an_unambiguous_existing_root_can_repair_entry():
    from agent_builder.workflow import normalize_connections
    workflow={'entry':'mistyped','nodes':[node('read','read_table'),node('save','write_result',[bind('table','read','table')],format='csv')],'edges':[edge('read','save')]}
    assert normalize_connections(workflow)['entry']=='read'
    ambiguous={'entry':'mistyped','nodes':[node('a','finish'),node('b','finish')],'edges':[]}
    assert normalize_connections(ambiguous)['entry']=='mistyped'
