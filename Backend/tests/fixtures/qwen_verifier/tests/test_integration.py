"""실제 계약·오류 경로·DB 버전·경계값을 독립적으로 대조한다."""
import copy,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
from pydantic import ValidationError
from jsonschema import Draft202012Validator
from a4b_verifier.contracts import VerificationInput,VerificationOutput,VerificationResult
from a4b_verifier.service import verify
from a4b_verifier.validation import code_checks,output_checks,pointer,canonical
from a4b_verifier.adapters import SQLiteStore,repository_roundtrip,draft_from_backend,load_registry
from a4b_verifier.client import OllamaConfig,call_qwen

ROOT=Path(__file__).resolve().parents[1]
CASES=json.loads((ROOT/'examples/cases.json').read_text())['cases']
def read_case(c):
    return VerificationInput.model_validate_json((ROOT/'examples'/c['input']).read_text()),json.loads((ROOT/'examples'/c['fixture']).read_text())

class Integration(unittest.TestCase):
    def test_13_fixture_routes(self):
        for c in CASES:
            with self.subTest(case=c['id']):
                x,y=read_case(c);result=verify(x,mode='fixture',fixture=y)
                self.assertEqual(result.route,c['expected']['route'])
                self.assertFalse(result.execution_allowed)
                self.assertTrue(set(c['expected']['required_codes'])<={f.code for f in result.findings})
                self.assertIsNone(result.error_code)

    def test_independent_json_schema_and_frozen_exports(self):
        for name,model in [('input',VerificationInput),('output',VerificationOutput),('result',VerificationResult)]:
            frozen=json.loads((ROOT/'schemas'/(name+'.schema.json')).read_text())
            self.assertEqual({k:v for k,v in frozen.items() if k!='$schema'},model.model_json_schema())
            Draft202012Validator.check_schema(frozen)
        vi=Draft202012Validator(json.loads((ROOT/'schemas/input.schema.json').read_text()))
        vo=Draft202012Validator(json.loads((ROOT/'schemas/output.schema.json').read_text()))
        vr=Draft202012Validator(json.loads((ROOT/'schemas/result.schema.json').read_text()))
        for c in CASES:
            x,y=read_case(c);vi.validate(x.model_dump(mode='json'));vo.validate(y)
            vr.validate(verify(x,mode='fixture',fixture=y).model_dump(mode='json'))

    def test_sqlite_exact_document_version_roundtrip(self):
        x,y=read_case(CASES[4])
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteStore(Path(d)/'demo.sqlite')
            try:
                fetched=repository_roundtrip(x,store);self.assertEqual(fetched,x)
                result=verify(fetched,mode='fixture',fixture=y);store.save_result(result)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM verifier_runs').fetchone()[0],1)
                altered=x.documents[0].model_copy(deep=True);altered.chunks[0].text='변경된 내용'
                with self.assertRaises(ValueError):store.save_document(altered)
                with self.assertRaises(KeyError):store.load_document('transactions','missing-version')
            finally:store.close()

    def test_independent_numeric_boundary_oracle(self):
        # Does not call package filter validation; compare hand-authored row IDs.
        for idx in [0,5]:
            c=CASES[idx];x,_=read_case(c)
            f=x.candidate_draft.workflow.nodes[1].settings['execution_plan']['filters'][0]
            rows=x.documents[0].tables[0].sample_rows
            selected=[]
            for r in rows:
                n=Decimal(str(r[f['field']]));v=Decimal(str(f['value']))
                hit=(n>=v) if f['operator']=='gte' else (n>v)
                if hit:selected.append(r['번호'])
            self.assertEqual(selected,c['expected']['row_ids'])
        x,_=read_case(CASES[4]);f=x.candidate_draft.workflow.nodes[1].settings['execution_plan']['filters'][0]
        rows=x.documents[0].tables[0].sample_rows
        self.assertEqual([r['번호'] for r in rows if r['보증금']>=2000000],['C','D'])
        self.assertEqual([r['번호'] for r in rows if r['보증금']>2000000],['D'])

    def test_ready_request_can_have_failed_draft(self):
        x,y=read_case(CASES[3]);result=verify(x,mode='fixture',fixture=y)
        self.assertEqual(result.model_output.request_status,'ready');self.assertEqual(result.route,'repair_plan')

    def test_reject_extra_fields_and_duplicate_message_id(self):
        x,y=read_case(CASES[4]);raw=x.model_dump(mode='json');raw['execute_now']=True
        with self.assertRaises(ValidationError):VerificationInput.model_validate(raw)
        raw=x.model_dump(mode='json');raw['messages'].append(raw['messages'][0])
        with self.assertRaises(ValidationError):VerificationInput.model_validate(raw)
        y['workflow']={'generated':'not-allowed'}
        self.assertEqual(verify(x,mode='fixture',fixture=y).route,'manual_review')

    def test_schema_rejects_boolean_as_execution_permission(self):
        x,y=read_case(CASES[0]);raw=verify(x,mode='fixture',fixture=y).model_dump(mode='json');raw['execution_allowed']=True
        with self.assertRaises(ValidationError):VerificationResult.model_validate(raw)

    def test_fabricated_actual_and_invalid_pointer(self):
        x,y=read_case(CASES[4]);y['issues'][0]['actual']='lt'
        self.assertIn('fabricated_actual',{f.code for f in output_checks(x,VerificationOutput.model_validate(y))})
        y['issues'][0]['path']='/workflow/nodes/999/settings'
        self.assertEqual(verify(x,mode='fixture',fixture=y).route,'manual_review')
        self.assertEqual(pointer({'a/b':{'~x':1}},'/a~1b/~0x'),1)
        with self.assertRaises(ValueError):pointer({'a~':1},'/a~')

    def test_malformed_filter_and_nonfinite_input_do_not_pass(self):
        x,y=read_case(CASES[0]);x.candidate_draft.workflow.nodes[1].settings['execution_plan']['filters'][0]['field']=[]
        self.assertIn('invalid_filter_field',{f.code for f in code_checks(x)})
        x,_=read_case(CASES[0]);x.candidate_draft.workflow.nodes[1].settings['execution_plan']['filters'][0]['value']=float('nan')
        r=verify(x,mode='fixture',fixture=y);self.assertEqual(r.route,'manual_review');self.assertFalse(r.execution_allowed)

    def test_bad_binding_and_unknown_column(self):
        x,y=read_case(CASES[0]);x.candidate_draft.workflow.nodes[1].bindings[0].output='artifact'
        self.assertIn('unknown_port',{f.code for f in code_checks(x)})
        x,_=read_case(CASES[0]);x.candidate_draft.workflow.nodes[1].settings['execution_plan']['filters'][0]['field']='없는열'
        self.assertIn('unknown_column',{f.code for f in code_checks(x)})

    def test_missing_prior_draft_cannot_pass_reflection(self):
        x,y=read_case(CASES[5]);x.previous_draft=None
        self.assertIn('missing_reflection_inputs',{f.code for f in output_checks(x,VerificationOutput.model_validate(y))})
        self.assertEqual(verify(x,mode='fixture',fixture=y).route,'manual_review')

    def test_ready_with_missing_condition_is_review(self):
        x,y=read_case(CASES[1]);y['request_status']='ready'
        self.assertEqual(verify(x,mode='fixture',fixture=y).route,'manual_review')

    def test_timeout_incomplete_generation_and_context_limit(self):
        x,_=read_case(CASES[0])
        with patch('a4b_verifier.service.call_qwen',side_effect=RuntimeError('ollama_unreachable_or_timeout')):
            r=verify(x,config=OllamaConfig());self.assertEqual(r.route,'manual_review');self.assertIsNone(r.model_output)
        with patch('a4b_verifier.client.http_json',return_value={'done':True,'done_reason':'length'}):
            with self.assertRaisesRegex(RuntimeError,'incomplete_generation'):call_qwen(x,OllamaConfig())
        with self.assertRaisesRegex(RuntimeError,'context_budget_exceeded'):call_qwen(x,OllamaConfig(num_ctx=4096))

    def test_registry_and_backend_adapter(self):
        tools=load_registry();self.assertEqual(len({t.id for t in tools}),16)
        x,_=read_case(CASES[0]);raw=x.candidate_draft.model_dump(mode='json')
        node=raw['workflow']['nodes'][1];raw['execution_plan']=node['settings'].pop('execution_plan')
        projected=draft_from_backend(raw,draft_id='projected',version='v2')
        self.assertEqual(projected.workflow.nodes[1].settings['execution_plan']['filters'][0]['value'],1000000)

    def test_numeric_equivalence_not_operator_equivalence(self):
        self.assertEqual(canonical(2000000),canonical('2000000.0'))
        self.assertNotEqual(canonical('gt'),canonical('gte'))

    def test_aggregate_output_schema_and_sort_validation(self):
        x,y=read_case(CASES[0]);wf=x.candidate_draft.workflow
        aggregate=wf.nodes[1].model_copy(deep=True);aggregate.tool='aggregate_table'
        aggregate.settings={'fields':[],'aggregation':'sum','sum_field':'보증금'}
        wf.nodes.insert(1,aggregate.model_copy(update={'id':'aggregate'}))
        wf.nodes[2].bindings[0].node_id='aggregate'
        wf.nodes[2].settings['execution_plan']['filters'][0]['field']='sum'
        wf.edges[0].target='aggregate'
        wf.edges.append(wf.edges[0].model_copy(update={'source':'aggregate','target':'filter'}))
        self.assertEqual(code_checks(x),[])
        wf.nodes[2].settings['execution_plan']['sort']=[{'field':'없는열','direction':'descending','value_type':'number'}]
        self.assertIn('unknown_sort_column',{f.code for f in code_checks(x)})

    def test_reflection_requires_latest_answer_source(self):
        x,y=read_case(CASES[5]);y['evidence']=[{'evidence_id':'user_request','quote':x.user_request}]
        self.assertIn('missing_latest_answer_evidence',{f.code for f in output_checks(x,VerificationOutput.model_validate(y))})

if __name__=='__main__':unittest.main()
