"""Local synthetic fixtures only; no model, external API or real user data."""
import copy
import json
import unittest

from agent_builder.models import AgentDraft, NodeSettings, WorkflowNode
from agent_builder.registry import TOOLS
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import compile_workflow, execute_node, run_workflow

def settings(**kw):
    values=dict(file_ids=[], query=None, fields=[], instructions=None, format=None,
        question=None, condition_field=None, condition_operator=None)
    return NodeSettings(**{**values,**kw}).model_dump()

def node(id, tool, config, bindings=None):
    return {'id':id, 'tool':tool, 'label':id, 'settings':config, 'bindings':bindings or []}

def bind(id):
    return [{'input':'table','node_id':id,'output':'table'}]

class Storage:
    def __init__(self,path): self.path=path
    def asset(self,id): return {'id':id,'name':'sample.csv','kind':'table','columns':['price','qty'],'hash':'fixture'}
    def rows(self,id): return [{'price':10,'qty':3},{'price':2.5,'qty':4}]

class CalculateColumnsIntegration(unittest.TestCase):
    def test_registered_and_legacy_schema_loads(self):
        self.assertIn('calculate_columns', {x['id'] for x in TOOLS})
        old=settings();old.pop('table_operation');old.pop('calculation_formula');old.pop('output_column');old.pop('rename_from');old.pop('rename_to')
        self.assertIsNone(NodeSettings.model_validate(old).table_operation)
        n=node('calc','calculate_columns',settings(table_operation='compute', calculation_formula='price*qty',output_column='total'),bind('read'))
        self.assertEqual(WorkflowNode.model_validate(n).tool,'calculate_columns')

    def test_end_to_end_local_read_compute_rename_and_json(self):
        calc=node('calc','calculate_columns',settings(table_operation='compute', calculation_formula='price*qty',output_column='total'),bind('read'))
        rename=node('rename','calculate_columns',settings(table_operation='rename',rename_from='total',rename_to='amount'),bind('calc'))
        nodes=[node('read','read_table',settings()),calc,rename,node('save','write_result',settings(format='json'),bind('rename'))]
        workflow={'entry':'read','nodes':nodes,'edges':[{'source':a['id'],'target':b['id'],'branch':'next'} for a,b in zip(nodes,nodes[1:])]}
        asset=Storage('unused').asset('fixture');self.assertEqual(workflow_errors(workflow,['read_table','calculate_columns','write_result'],[asset]),[])
        storage=Storage(':memory:')
        config={'workflow':workflow,'tool_ids':['read_table','calculate_columns','write_result'],'document_ids':['fixture'],'execution_plan':{'output_format':'json'}}
        graph,connection=compile_workflow(storage,config,None)
        try:
            out=graph.invoke({'request':'fixture','file_ids':['fixture'],'outputs':{},'steps':[]}, {'configurable':{'thread_id':'synthetic'}})
        finally:connection.close()
        self.assertEqual(json.loads(out['outputs']['save']['artifact']['content']),[{'price':10,'qty':3,'amount':30},{'price':2.5,'qty':4,'amount':10}])
        self.assertEqual(out['outputs']['rename']['table']['file_id'],'fixture')
        self.assertEqual(out['outputs']['rename']['table']['columns'],['price','qty','amount'])

    def test_rejects_unsafe_formula_and_preserves_input(self):
        original={'columns':['price'],'rows':[{'price':1}],'file_id':'fixture','review_report':True}
        state={'outputs':{'read':{'table':copy.deepcopy(original)}}}
        calc=node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='__import__("os").system("ignored")',output_column='total'),bind('read'))
        with self.assertRaises(ValueError): execute_node(calc,state,None,{},None)
        self.assertEqual(state['outputs']['read']['table'],original)

    def test_empty_table_and_collision(self):
        state={'outputs':{'read':{'table':{'columns':['price'],'rows':[],'file_id':'fixture','review_report':True}}}}
        calc=node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='price*2',output_column='total'),bind('read'))
        result=execute_node(calc,state,None,{},None)['table']
        self.assertEqual(result['columns'],['price','total']);self.assertTrue(result['review_report']);self.assertEqual(result['file_id'],'fixture')
        calc['settings']['output_column']='price'
        with self.assertRaises(ValueError):execute_node(calc,state,None,{},None)

    def test_schema_validation_rejects_missing_input_and_bad_formula(self):
        nodes=[node('read','read_table',settings()),node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='price()',output_column='total'))]
        flow={'entry':'read','nodes':nodes,'edges':[{'source':'read','target':'calc','branch':'next'}]}
        messages=workflow_errors(flow,['read_table','calculate_columns'],[])
        self.assertTrue(any('필수 입력' in m for m in messages));self.assertTrue(any('수식' in m for m in messages))

    def test_text_replace_uses_existing_literal_operation(self):
        source={'outputs':{'prior':{'text':'상품 상품입니다.'}}}
        transform=node('transform','text_transform',settings(text_find='상품',text_replacement='제품'),
            [{'input':'text','node_id':'prior','output':'text'}])
        self.assertEqual(execute_node(transform,source,None,{},None),{'text':'제품 제품입니다.'})
        self.assertEqual(source['outputs']['prior']['text'],'상품 상품입니다.')
        transform['settings']['text_find']=''
        with self.assertRaises(ValueError):execute_node(transform,source,None,{},None)

    def test_explicit_date_format_and_timezone(self):
        table={'columns':['date','name'],'rows':[{'date':'2026-11-01','name':'A'}],'file_id':'fixture'}
        source={'outputs':{'read':{'table':copy.deepcopy(table)}}}
        transform=node('dates','calculate_columns',settings(table_operation='parse_dates',fields=['date'],
            date_input_format='%Y-%m-%d',date_timezone='Asia/Seoul'),bind('read'))
        result=execute_node(transform,source,None,{},None)['table']
        self.assertEqual(result['rows'][0]['date'],'2026-11-01T00:00:00+09:00')
        self.assertEqual(result['file_id'],'fixture');self.assertEqual(source['outputs']['read']['table'],table)
        transform['settings']['date_timezone']=None
        with self.assertRaises(ValueError):execute_node(transform,source,None,{},None)

    def test_base64_roundtrip_and_text_sha256(self):
        import hashlib
        source={'outputs':{'prior':{'text':'가상 text'}}}
        binding=[{'input':'text','node_id':'prior','output':'text'}]
        encode=node('encode','base64_codec',settings(codec_mode='encode'),binding)
        encoded=execute_node(encode,source,None,{},None)['text']
        source['outputs']['encoded']={'text':encoded}
        decode=node('decode','base64_codec',settings(codec_mode='decode'),[{'input':'text','node_id':'encoded','output':'text'}])
        self.assertEqual(execute_node(decode,source,None,{},None)['text'],'가상 text')
        digest=node('hash','text_hash',settings(),binding)
        self.assertEqual(execute_node(digest,source,None,{},None)['text'],hashlib.sha256('가상 text'.encode()).hexdigest())
        encode['settings']['codec_mode']=None
        with self.assertRaises(ValueError):execute_node(encode,source,None,{},None)

    def test_text_replace_in_registered_workflow(self):
        nodes=[node('source','agent_node',settings()),
            node('replace','text_transform',settings(text_find='old',text_replacement='new'),
                [{'input':'text','node_id':'source','output':'text'}]),
            node('finish','finish',settings(),[{'input':'text','node_id':'replace','output':'text'}])]
        workflow={'entry':'source','nodes':nodes,'edges':[{'source':a['id'],'target':b['id'],'branch':'next'} for a,b in zip(nodes,nodes[1:])]}
        config={'workflow':workflow,'tool_ids':['agent_node','text_transform','finish'],'document_ids':[],'execution_plan':{'output_format':'json'}}
        self.assertEqual(workflow_errors(workflow,config['tool_ids'],[]),[])
        def executor(n,state,storage,config,llm):
            return {'text':'old value'} if n['tool']=='agent_node' else execute_node(n,state,storage,config,llm)
        graph,connection=compile_workflow(Storage(':memory:'),config,None,executor=executor)
        try:out=graph.invoke({'request':'fixture','file_ids':[],'outputs':{},'steps':[]}, {'configurable':{'thread_id':'text'}})
        finally:connection.close()
        self.assertEqual(out['outputs']['finish']['text'],'new value')

def test_saved_service_workflow_and_report(tmp_path):
    from agent_builder.storage import Storage, uid
    from agent_builder.test_builder import draft, initial
    storage=Storage(tmp_path)
    asset={'id':uid(),'name':'synthetic.csv','kind':'table','format':'csv','columns':['price','qty'],
        'profile':{},'row_count':2,'preview':[],'text':''}
    rows=[{'price':10,'qty':3},{'price':2.5,'qty':4}]
    storage.add_asset(asset,rows)
    nodes=[node('read','read_table',settings()),
        node('calc','calculate_columns',settings(table_operation='compute',calculation_formula='price*qty',output_column='total'),bind('read')),
        node('rename','calculate_columns',settings(table_operation='rename',rename_from='total',rename_to='amount'),bind('calc')),
        node('save','write_result',settings(format='json'),bind('rename'))]
    workflow={'entry':'read','nodes':nodes,'edges':[{'source':a['id'],'target':b['id'],'branch':'next'} for a,b in zip(nodes,nodes[1:])]}
    config={**draft(),'tool_ids':['read_table','calculate_columns','write_result'],'document_ids':[asset['id']],
        'workflow':workflow,'schedule':None,'report_destination':None}
    state={**initial(),'draft':AgentDraft.model_validate(config).model_dump(),'confirmed':True}
    agent_id=storage.save_agent(state)
    result=run_workflow(storage,agent_id,'가상 표 계산','unused',file_ids=[asset['id']])
    assert result['status']=='succeeded', result.get('error_type')
    assert result['rows']==[{'price':10,'qty':3,'amount':30},{'price':2.5,'qty':4,'amount':10}]
    assert json.loads((tmp_path/result['report_path']).read_text(encoding='utf-8'))==result['rows']
    assert storage.rows(asset['id'])==rows
    storage.close()

def test_saved_text_workflow_shows_transformed_result_only(tmp_path):
    from agent_builder.storage import Storage
    from agent_builder.test_builder import draft, initial
    storage=Storage(tmp_path)
    nodes=[node('source','read_document',settings()),
        node('answer','compose_answer',settings(),[{'input':'documents','node_id':'source','output':'documents'}]),
        node('replace','text_transform',settings(text_find='old',text_replacement='new'),
            [{'input':'text','node_id':'answer','output':'text'}])]
    workflow={'entry':'source','nodes':nodes,'edges':[{'source':a['id'],'target':b['id'],'branch':'next'} for a,b in zip(nodes,nodes[1:])]}
    config={**draft(),'tool_ids':['read_document','compose_answer','text_transform'],'document_ids':[],
        'workflow':workflow,'schedule':None,'report_destination':None}
    state={**initial(),'draft':AgentDraft.model_validate(config).model_dump(),'confirmed':True}
    agent_id=storage.save_agent(state)
    def executor(n,state,storage,config,llm):
        if n['tool']=='read_document':return {'documents':[{'text':'fixture'}]}
        if n['tool']=='compose_answer':return {'text':'old value'}
        return execute_node(n,state,storage,config,llm)
    result=run_workflow(storage,agent_id,'가상 문자열','unused',executor=executor,llm=object())
    assert result['status']=='succeeded', result.get('error_type')
    assert result['answer']=='new value'
    storage.close()

if __name__=='__main__':unittest.main()
