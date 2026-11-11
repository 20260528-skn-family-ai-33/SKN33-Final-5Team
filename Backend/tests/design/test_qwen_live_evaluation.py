"""Offline evaluation-instrumentation tests; these are NOT Qwen results."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/verify'))
from qwen_live_support import sanitize, validate_corpus, metrics, choose_answer, can_execute_locally
from workflow_engine.catalog.registry import TOOLS


def test_frozen_corpus_contains_valid_catalog_and_ten_actual_clarification_loops():
    corpus=json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    assert validate_corpus(corpus,{t['id']:t for t in TOOLS})
    assert len(corpus['cases'])==52
    corpus['cases'][0]['gold']['routing']='UNSUPPORTED'
    with pytest.raises(ValueError,match='digest'):validate_corpus(corpus,{t['id']:t for t in TOOLS})


def test_zero_live_results_are_null_not_perfect_scores():
    result=metrics([{'status':'not_run','live_model_calls':0}]*52)
    assert all(m['value'] is None and m['denominator']==0 for m in result.values())


def test_live_failure_counts_as_routing_false_negative():
    rows=[{'status':'completed','live_model_calls':1,'expected_route':'READY','actual_route':'READY'},
          {'status':'failed','live_model_calls':1,'expected_route':'READY','actual_route':'SYSTEM_ERROR'}]
    score=metrics(rows)['routing_macro_f1']
    assert score['denominator']==2 and score['value']==pytest.approx(2/3)


def test_secrets_and_real_addresses_are_removed_but_synthetic_gold_preserved():
    text=sanitize({'content':'opaque-secret Bearer abc.def audit@example.invalid person@example.com',
                   'Authorization':'opaque-secret','api_key':'opaque-secret'},['opaque-secret'])
    assert 'opaque-secret' not in str(text) and 'abc.def' not in str(text)
    assert 'person@example.com' not in str(text) and 'audit@example.invalid' in str(text)
    assert 'Authorization' not in text and 'api_key' not in text


def test_answer_policy_depends_on_actual_question_and_does_not_repeat():
    case={'answer_policy':[{'slot':'threshold','question_terms':['기준 값'],'text':'20 이상'}]}
    assert choose_answer(case,{'text':'받는 주소?'},set())['status']=='unmatched_question'
    assert choose_answer(case,{'text':'기준 값은?'},set())['text']=='20 이상'
    assert choose_answer(case,{'text':'기준 값은?'},{'threshold'})['status']=='repeated_question'


@pytest.mark.parametrize('tool',['email.send','email.list','agent_node','saved_workflow','calendar.create_event'])
def test_external_and_nested_execution_is_never_allowlisted(tool):
    assert not can_execute_locally({'workflow':{'nodes':[{'tool':tool,'settings':{}}]}})


def test_operation_paths_cannot_escape_local_execution_policy():
    assert not can_execute_locally({'workflow':{'nodes':[{'tool':'read_table','settings':{'operation':'read_csv','arguments_json':'{"path":"/private"}'}}]}})
    assert can_execute_locally({'workflow':{'nodes':[{'tool':'read_table','settings':{}},{'tool':'write_result','settings':{}}]}})


def test_adjudication_does_not_count_wrong_ready_as_clarification_completion():
    spec=importlib.util.spec_from_file_location('adjudicate',ROOT/'scripts/verify/adjudicate-qwen-live.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    corpus=json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    case=next(c for c in corpus['cases'] if c['id']=='M01')
    record={'id':'M01','final_route':'READY','turns':[{'response':{}},{'response':{}}],'workflow':None}
    label={'intent_correct':True,'observed_slots':{'filter.field':'wrong','output.format':'json'},
           'final_observed_slots':{},'questions':[],'task_coverage':[False,False,False],'evidence':'Synthetic scorer fixture'}
    result=mod.score(record,case,label)['adjudicated_metrics']
    assert result['slot_extraction_f1']=={'numerator':2,'denominator':4}
    assert result['clarification_completion_rate']=={'numerator':0,'denominator':1}
    assert result['answer_incorporation_accuracy']=={'numerator':0,'denominator':2}
    assert result['requirement_coverage']=={'numerator':0,'denominator':3}
