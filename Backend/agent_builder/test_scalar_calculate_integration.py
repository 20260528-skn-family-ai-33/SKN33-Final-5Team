import pytest
from agent_builder.executors.scalar_calculate import execute
from agent_builder.workflow_runtime import execute_node,validate_node_result
from agent_builder.nodes.verification import current_tools
from agent_builder.models import NodeSettings
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import compile_workflow

def test_scalar_registered_executor_and_numeric_result():
    node={'tool':'scalar_calculate','settings':{'calculation_formula':'(8 + 4) * 2'}}
    result=execute_node(node,{},None,None,None)
    assert result=={'text':'24'}
    validate_node_result('scalar_calculate',result)
    assert any(t['id']=='scalar_calculate' for t in current_tools())

def test_scalar_real_workflow_execution_with_output_port():
    values={'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,
            'question':None,'condition_field':None,'condition_operator':None,'calculation_formula':'(8+4)*2'}
    node={'id':'calc','tool':'scalar_calculate','label':'계산','settings':NodeSettings.model_validate(values).model_dump(),'bindings':[]}
    flow={'entry':'calc','nodes':[node],'edges':[]}
    assert workflow_errors(flow,['scalar_calculate'],[])==[]
    from types import SimpleNamespace
    graph,connection=compile_workflow(SimpleNamespace(path=':memory:'),{'workflow':flow,'tool_ids':['scalar_calculate'],'document_ids':[],
                                            'execution_plan':{'output_format':'json'}},None)
    try:out=graph.invoke({'request':'계산','file_ids':[],'outputs':{},'steps':[]},{'configurable':{'thread_id':'synthetic-scalar'}})
    finally:connection.close()
    assert out['outputs']['calc']['text']=='24'

def test_verifier_uses_literal_user_formula_without_requiring_a_document():
    from a4b_verifier.contracts import VerificationInput
    from a4b_verifier.validation import code_checks
    data=VerificationInput.model_validate({'contract_version':'a4b.verifier.v1','request_id':'scalar',
        'user_request':'수식 (8 + 4) * 2를 계산해 주세요.','tools':current_tools(),
        'candidate_draft':{'id':'d','version':'1','workflow':{'entry':'calc','nodes':[
            {'id':'calc','tool':'scalar_calculate','settings':{'calculation_formula':'(8+4)*2'}}]}}})
    codes={x.code for x in code_checks(data)}
    assert 'evidence_incomplete' not in codes and 'scalar_formula_not_in_request' not in codes
    data.candidate_draft.workflow.nodes[0].settings['calculation_formula']='8+4'
    assert 'scalar_formula_not_in_request' in {x.code for x in code_checks(data)}

@pytest.mark.parametrize('formula',['x+1','__import__("os")','1/0','1e999'])
def test_scalar_rejects_variables_code_and_invalid_math(formula):
    with pytest.raises((ValueError,KeyError,ZeroDivisionError,ArithmeticError)):
        execute({'settings':{'calculation_formula':formula}},{},None,None,None)
