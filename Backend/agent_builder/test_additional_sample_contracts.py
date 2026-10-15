import pytest
from agent_builder.test_workflow import node,bind
from agent_builder.executors.aggregate_table import execute

@pytest.mark.parametrize('value,expected',[('1,250,000',1250000),('-12,500.25',-12500.25),('0',0)])
def test_sum_accepts_valid_thousands_groups(value,expected):
    n=node('sum','aggregate_table',[bind('table','read','table')],fields=[],aggregation='sum',sum_field='amount')
    state={'outputs':{'read':{'table':{'columns':['amount'],'rows':[{'amount':value}],'file_id':'fixture'}}}}
    assert execute(n,state,None,{},None)['table']['rows']==[{'sum':expected}]

@pytest.mark.parametrize('value',['1,25','1,250,00','NaN','Infinity',True,''])
def test_sum_rejects_malformed_numbers(value):
    n=node('sum','aggregate_table',[bind('table','read','table')],fields=[],aggregation='sum',sum_field='amount')
    state={'outputs':{'read':{'table':{'columns':['amount'],'rows':[{'amount':value}],'file_id':'fixture'}}}}
    with pytest.raises(ValueError):execute(n,state,None,{},None)




@pytest.mark.parametrize('content,valid',[('{"name":"고객"}',True),('안내 {"name":"고객"}',False)])
def test_json_text_export_validates_document(content,valid):
    from agent_builder.executors.export_result import execute
    n=node('save','export_result',[bind('text','compose','text')],format='json')
    state={'outputs':{'compose':{'text':content}}};config={'workflow':{'nodes':[n],'edges':[]}}
    if valid:
        result=execute(n,state,None,config,None)
        assert result['artifact']['name'].endswith('.json')
    else:
        with pytest.raises(ValueError):execute(n,state,None,config,None)










def test_output_summary_uses_actual_writer_format():
    from agent_builder.models import ExecutionPlan
    from agent_builder.presentation import canonical_table_draft
    plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json').model_dump()
    draft={'workflow':{'entry':'read','nodes':[node('read','read_table'),node('save','write_result',[bind('table','read','table')],format='csv')],'edges':[]},'execution_plan':plan}
    result=canonical_table_draft(draft,[{'role':'user','content':'CSV로 저장'}],[])
    assert '출력: CSV' in result['instructions']
    assert result['workflow']['nodes'][1]['settings']['format']=='csv'
