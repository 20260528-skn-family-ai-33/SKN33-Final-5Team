"""Clarification eval with a real local Ollama model through the service message/confirm entry.

usage: python run_eval.py <backend_dir> <model> <label> <cache.json> <out.json> [cases.json]
env:   EVAL_BASE_URL (Ollama /v1 URL), any EVAL_A4B_<NAME> is passed as A4B_<NAME>,
       EVAL_ORCH=off|shadow|enforce sets A4B_ORCHESTRATION_MODE (shadow records, enforce asks).
Draft gold labels: question target keywords and required/not-required, to be reviewed by the team.
The capability/condition checks call the real model. The workflow planner is a fixed stub
(a valid draft with no questions), so 'ready' here means "the clarification gates let it
through", not that a real planner produced a correct workflow.
Model responses are cached by request hash, so main and the fix see identical outputs
for identical requests (same model and generation settings).
"""
import hashlib, json, os, sys, time
from tempfile import TemporaryDirectory

backend, MODEL, LABEL, CACHE, OUT = sys.argv[1:6]
CASES_FILE = sys.argv[6] if len(sys.argv) > 6 else None
sys.path.insert(0, backend); os.chdir(backend)
for k in tuple(os.environ):
    if k.startswith(('A4B_', 'EASYFLOW_')): del os.environ[k]
os.environ.update(A4B_MAIN_PROVIDER='ollama', A4B_MAIN_BASE_URL=os.getenv('EVAL_BASE_URL','http://localhost:11434/v1'), A4B_STORAGE='sqlite',
                  A4B_ATTACHMENT_PRECHECK='on', A4B_PLANNER_MODE='deepagent', A4B_TOOL_ADVICE_MODE='off',
                  A4B_REQUIREMENT_MODE='off', A4B_MAIN_TIMEOUT_S='600')

if os.getenv('EVAL_ORCH'): os.environ['A4B_ORCHESTRATION_MODE']=os.getenv('EVAL_ORCH')
# Every EVAL_A4B_* variable is passed to the app as A4B_* (e.g. EVAL_A4B_ORCHESTRATION_MODEL).
for key, value in list(os.environ.items()):
    if key.startswith('EVAL_A4B_'): os.environ[key[len('EVAL_'):]] = value
from agent_builder.design.nodes import direct_conversation as dc
if os.getenv('EVAL_OFFLINE') == '1':
    # Offline: never call a model. A stage that needs one records a retryable system failure,
    # so the run shows which cases are decided by code and which depend on the model.
    def _no_model(self, messages):
        raise RuntimeError('MODEL_NEEDED')
    dc.OllamaPlanner.invoke = _no_model
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
stats = {'live': 0, 'cached': 0, 'seconds': 0.0}
_orig = dc.OllamaPlanner.invoke
def cached_invoke(self, messages):
    key = hashlib.sha256(json.dumps([self.model, messages, self.schema], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if key in cache:
        stats['cached'] += 1
        return cache[key]
    t = time.time(); value = _orig(self, messages); stats['seconds'] += time.time() - t
    stats['live'] += 1
    cache[key] = {'content': value['content'], 'metrics': value.get('metrics', {})}
    json.dump(cache, open(CACHE, 'w'), ensure_ascii=False)
    return value
dc.OllamaPlanner.invoke = cached_invoke

import service.handlers.design as worker
from service.worker import handle
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from tests.design.test_runtime_file_questions import question_draft
from tests.workflow_engine.test_workflow import node, bind, edge
_orig_build = worker.build_graph
def build(*a, **k):
    k.update(capability_mode='enforce', condition_mode='enforce', verifier_mode='off')
    return _orig_build(*a, **k)
worker.build_graph = build

PLANNER_CALLS = [0]


def stub_planner(s):
    PLANNER_CALLS[0] += 1
    first = next(m['content'] for m in s['messages'] if m['role'] == 'user')
    ns = [node('read', 'read_document'), node('answer', 'compose_answer', [bind('documents', 'read', 'documents')], instructions='요청대로 답변')]
    d = question_draft('x')
    d.update(name='업무 처리', purpose='요청 처리', tool_ids=[n['tool'] for n in ns], questions=[], question_options=[],
             requirements=[dict(description='요청', source_id='user', quote=first)],
             workflow=dict(entry='read', nodes=ns, edges=[edge('read', 'answer')]))
    # Increment like the real planners so a validation repair loop stays bounded.
    return dict(draft=d, confirmed=False, repair_count=s.get('repair_count', 0) + 1)

TABLE_ASSET = {'name': 'sales.csv', 'kind': 'table', 'format': 'csv',
               'columns': ['customer_id', 'region', 'qty', 'unit_price', 'amount'],
               'profile': {c: {'types': ['number']} for c in ('qty', 'unit_price', 'amount')}}
TABLE_ROWS = [{'customer_id': 'c1', 'region': '서울', 'qty': 2, 'unit_price': 10, 'amount': 20},
              {'customer_id': 'c1', 'region': '부산', 'qty': 1, 'unit_price': 15, 'amount': 15},
              {'customer_id': 'c2', 'region': '서울', 'qty': 3, 'unit_price': 9, 'amount': 27}]
POLICY_ASSET = {'name': 'policy.txt', 'kind': 'document', 'format': 'txt', 'pages': ['환불 규정'],
                'text': '환불 규정: 구매 후 7일 이내 미사용 상품은 전액 환불합니다. 개봉 상품은 부분 환불합니다.'}
ASSETS = {'table': (TABLE_ASSET, TABLE_ROWS), 'policy': (POLICY_ASSET, [])}
CASES = json.load(open(CASES_FILE or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cases_v2.json'), encoding='utf-8'))
results = []
# Uploaded assets get IDs that appear in model prompts; fixed IDs per case keep prompts identical
# between runs so cached model responses can be replayed (EVAL_OFFLINE=1).
import itertools
for case in CASES:
    _ids = itertools.count(1)
    worker.uid = lambda: f"asset-{case['id']}-{next(_ids)}"
    rows = []
    with TemporaryDirectory() as d:
        s = handle({'directory': d, 'action': 'new_session', 'model': MODEL, 'credential_id': uid()})
        for kind in case.get('resources', []):
            asset, asset_rows = ASSETS[kind]
            s = handle({'directory': d, 'id': s['id'], 'revision': s['revision'], 'action': 'upload', 'asset': dict(asset), 'rows': asset_rows})
        for turn in case['turns']:
            before_calls = stats['live'] + stats['cached']
            planner_before = PLANNER_CALLS[0]
            t = time.time(); prev_ids = [q.get('id') for q in s.get('pending_questions') or []]
            try:
                fields = {'content': turn['text']} if turn['action'] == 'message' else {}
                if case.get('gmail'): fields['gmail_connected'] = True
                s = handle({'directory': d, 'id': s['id'], 'revision': s['revision'], 'action': turn['action'], **fields},
                           conversation=stub_planner)
                store = Storage(d); saved = len(store.agents()); store.close()
                draft = s.get('draft') or {}
                questions = [q['text'] for q in s.get('pending_questions') or []] or (
                    list(draft.get('questions') or []) if s['status'] == 'needs_clarification' else [])
                rows.append(dict(action=turn['action'], text=turn.get('text'), status=s['status'], questions=questions,
                                 same_question_ids=bool(prev_ids) and prev_ids == [q.get('id') for q in s.get('pending_questions') or []],
                                 has_draft=bool(s.get('draft')), saved=saved, system_failure=(s.get('system_failure') or {}).get('stage'),
                                 tools=[n['tool'] for n in ((draft.get('workflow') or {}).get('nodes') or [])], draft_name=draft.get('name'), planner_reached=PLANNER_CALLS[0] > planner_before, rs=({'issues':(s.get('requirement_state') or {}).get('issues'),'tasks':len((s.get('requirement_state') or {}).get('tasks',[])),'conditions':len((s.get('requirement_state') or {}).get('conditions',[])),'inputs':[(i['subject'],i['timing']) for i in (s.get('requirement_state') or {}).get('inputs',[])],'questions':len((s.get('requirement_state') or {}).get('questions',[])),'error':(s.get('requirement_state') or {}).get('error')} if s.get('requirement_state') else None), issues=(s.get('issues') or [])[:2], decision=(s.get('capability_assessment') or {}).get('decision'),
                                 model_calls=stats['live'] + stats['cached'] - before_calls, seconds=round(time.time() - t, 1)))
            except Exception as error:
                rows.append(dict(action=turn['action'], text=turn.get('text'), error=f'{type(error).__name__}: {str(error)[:160]}',
                                 model_calls=stats['live'] + stats['cached'] - before_calls, seconds=round(time.time() - t, 1)))
            print(LABEL, case['id'], rows[-1].get('status') or rows[-1].get('error'), rows[-1].get('questions'), flush=True)
    results.append({**case, 'rows': rows})
json.dump({'model': MODEL, 'label': LABEL, 'stats': stats, 'results': results}, open(OUT, 'w'), ensure_ascii=False, indent=1)
print('done', LABEL, stats)
