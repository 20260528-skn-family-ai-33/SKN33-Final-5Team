import importlib.util
import json
from pathlib import Path
import sys

import pytest

VERIFY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(VERIFY))
spec=importlib.util.spec_from_file_location('ledger_scoring',VERIFY/'score-qwen-ledgers.py')
scoring=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


def fixture():
    case={'id':'F','gold':{'final_routing':'READY','workflow':{'tasks':[{'id':'read'},{'id':'compute'}]}}}
    corpus={'cases':[case],'gold_sha256':'fixture'}
    call={'stage':'requirements','status':'returned','raw_output':{'content':json.dumps({'tasks':[{},{}]})}}
    row={'id':'F','session_id':'s','thread_id':'s','status':'completed','model_calls':[call],
         'stages':[{'stage':'requirements','parsed_state':{'requirement_ledger':{'tasks':[{}]}}},
                   {'stage':'validate','parsed_state':{'requirement_ledger':{'tasks':[{}]}}}],
         'turns':[{'response':{'id':'s','revision':1,'status':'needs_clarification','messages':[1,2],
                               'pending_questions':[{'id':'q1','text':'Threshold?'}]}},
                  {'response':{'id':'s','revision':2,'status':'validation_failed','messages':[1,2,3,4],
                               'pending_questions':[],'draft':{'questions':['Threshold?']}}}],
         'turn_attempts':[{'turn_index':1,'status':'returned','question_id':'q1','prior_pending_question_ids':['q1']}]}
    labels=scoring.review_template([row],corpus)
    label=labels['cases']['F']
    label.update(evidence='fixture stage and raw call',owner_checks=[{
        'candidate_call_index':0,'owner_key':'threshold','candidate_conflict':True,
        'accepted_conflict':False,'conflicting_candidate_retained':False,'evidence':'synthetic discrepancy rejected'}],
        duplicate_candidate_ids=['call:0/task:1'],duplicate_accepted_ids=[],required_work={'read':'present','compute':'omitted'})
    return row,corpus,labels


def test_rejected_claim_duplicate_candidate_and_omitted_work_use_distinct_denominators():
    row,corpus,labels=fixture()
    result=scoring.summarize([row],corpus,labels,True)
    m=result['metrics']
    assert m['candidate_owner_conflict_rate']==scoring.ratio(1,1)
    assert m['accepted_owner_conflict_rate']==scoring.ratio(0,1)
    assert m['candidate_duplicate_task_rate']==scoring.ratio(1,2)
    assert m['accepted_duplicate_task_rate']==scoring.ratio(0,1)
    assert m['accepted_ledger_task_omission_rate']==scoring.ratio(1,2)
    assert m['question_id_match_per_submitted_question_id']==scoring.ratio(1,1)
    assert m['stale_draft_reexposure_per_fallback_question']==scoring.ratio(1,1)
    assert [q['source'] for q in result['question_fields']]==['pending_questions','draft_fallback']
    assert result['backend_question_and_turn_counts']['prior_history_prefix_preserved']==1


def test_unreached_work_is_reported_without_creating_a_conditional_miss():
    row,corpus,labels=fixture();row['stages']=[]
    label=labels['cases']['F'];label['source_case_sha256']=scoring.digest(row)
    label['required_work']={'read':'not_reached','compute':'not_reached'}
    label['owner_checks'][0].update(accepted_conflict=None,conflicting_candidate_retained=None)
    result=scoring.summarize([row],corpus,labels,True)['metrics']
    assert result['accepted_ledger_task_omission_rate']==scoring.ratio(0,0)
    assert result['end_to_end_ledger_task_unrepresented_rate']==scoring.ratio(2,2)
    assert result['accepted_owner_conflict_rate']==scoring.ratio(0,0)


def test_labels_must_match_case_hash_known_units_and_all_fixed_obligations():
    row,corpus,labels=fixture();label=labels['cases']['F']
    label['duplicate_candidate_ids']=['call:9/task:9']
    with pytest.raises(ValueError,match='observed task units'):scoring.summarize([row],corpus,labels)
    label['duplicate_candidate_ids']=[];label['required_work'].pop('compute')
    with pytest.raises(ValueError,match='frozen required task'):scoring.summarize([row],corpus,labels)
    label['source_case_sha256']='old'
    with pytest.raises(ValueError,match='source hash'):scoring.summarize([row],corpus,labels)


def test_missing_review_has_no_semantic_denominator_and_requires_explicit_completion():
    row,corpus,labels=fixture();labels['cases']={}
    result=scoring.summarize([row],corpus,labels)
    assert result['unreviewed_case_ids']==['F']
    assert result['metrics']['candidate_owner_conflict_rate']['value'] is None
    with pytest.raises(ValueError,match='missing'):scoring.summarize([row],corpus,labels,True)


def test_sdk_assembled_text_and_invalid_candidates_stay_separate():
    row,_,_=fixture();payload=row['model_calls'][0]['raw_output']['content']
    row['model_calls'][0]['raw_output']=[{'content':[{'type':'text','text':payload}]}]
    row['model_calls'].append({'stage':'requirements','status':'returned','raw_output':{'content':'not JSON'}})
    observed=scoring.inventory(row)
    assert observed['candidate_task_ids']==['call:0/task:0','call:0/task:1']
    assert observed['unparsed_requirement_call_indices']==[1]
