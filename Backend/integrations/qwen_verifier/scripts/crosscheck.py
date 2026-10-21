"""Pydantic 외의 JSON Schema 검사기·수작업 정답·원시 결과를 교차 확인."""
import json,sys,hashlib
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from a4b_verifier.contracts import VerificationInput,VerificationOutput
from a4b_verifier.service import verify

def digest(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def main():
    cases=json.loads((ROOT/'examples/cases.json').read_text())['cases']
    schemas={s:Draft202012Validator(json.loads((ROOT/'schemas'/(s+'.schema.json')).read_text())) for s in ['input','output','result']}
    checked=[]
    # Explicit reference routing expectations; not imported from production choose_route.
    reference={'01_ready':'continue_review','02_missing_selection':'ask_user','03_ambiguous_column':'ask_user',
        '04_wrong_column':'repair_plan','05_reflection_wrong_operator':'repair_plan','06_reflection_correct':'continue_review',
        '07_unrelated_field_changed':'repair_plan','08_still_ambiguous':'ask_user','09_unsupported_email':'unsupported',
        '10_parser_failed':'manual_review','11_missing_filter_value':'repair_plan',
        '12_forged_evidence_guard':'manual_review','13_cycle_guard':'repair_plan'}
    for c in cases:
        raw=json.loads((ROOT/'examples'/c['input']).read_text());out=json.loads((ROOT/'examples'/c['fixture']).read_text())
        schemas['input'].validate(raw);schemas['output'].validate(out)
        result=verify(VerificationInput.model_validate(raw),mode='fixture',fixture=out)
        schemas['result'].validate(result.model_dump(mode='json'))
        assert result.route==reference[c['id']]==c['expected']['route']
        assert result.execution_allowed is False
        checked.append(c['id'])
    fixture=json.loads((ROOT/'reports/fixture_suite.json').read_text())
    assert fixture['mode']=='fixture' and fixture['cases']==fixture['passed']==13
    live=json.loads((ROOT/'reports/live_smoke.json').read_text())
    assert live['mode']=='live' and live['cases']==3 and live['model_digest_unchanged']
    assert all(digest(ROOT/path)==sha for path,sha in live['source_hashes'].items())
    independent=[]
    for r in live['records']:
        c=next(c for c in cases if c['id']==r['id']);assert digest(ROOT/'examples'/c['input'])==r['input_sha256']
        schemas['result'].validate(r['result'])
        raw=r['result']['model_output'];assert raw is not None;schemas['output'].validate(raw)
        # Re-run code checks on exactly the archived live output, not another model call.
        request=VerificationInput.model_validate_json((ROOT/'examples'/c['input']).read_text())
        rerun=verify(request,mode='fixture',fixture=raw)
        assert rerun.route==r['result']['route']
        assert [f.model_dump() for f in rerun.findings]==r['result']['findings']
        expected=c['expected']
        actual={'request_status':raw['request_status']==expected['request_status'],'draft_verdict':raw['draft_verdict']==expected['draft_verdict'],
            'reflection_verdict':raw['reflection_verdict']==expected['reflection_verdict'],
            'missing_conditions':{x['code'] for x in raw['missing_conditions']}==set(expected['missing_codes'])}
        assert all(actual[k]==r['checks'][k] for k in actual)
        independent.append({'id':r['id'],'semantic_axes_match':all(actual.values()),'route_matches_expected':rerun.route==expected['route'],
            'code_findings':[f.code for f in rerun.findings],'execution_allowed':False})
    unit=json.loads((ROOT/'reports/unit_tests.json').read_text());assert unit['passed'] and unit['tests_run']>=17
    report={'contract_cases_crosschecked':checked,'independent_jsonschema':True,'fixture_routes_all_match':True,
        'live_source_and_input_hashes_match':True,'live_replayed_code_checks_match':True,'live_semantic_review':independent,
        'unit_test_count':unit['tests_run'],'unit_tests_passed':True,'model_quality_gate_passed':all(r['semantic_axes_match'] for r in independent),
        'auto_execution_supported':False,'test_data':'수작업 합성 연동 예시. 독립 평가셋 아님','training_runs':0}
    (ROOT/'reports/crosscheck.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
