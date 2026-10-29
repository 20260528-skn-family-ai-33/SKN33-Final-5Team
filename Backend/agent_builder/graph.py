import sqlite3
import time
import json
import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .models import BuilderState
from .nodes.resources import resources_node
from .nodes.conversation import make_conversation
from .nodes.validation import validate_node
from .nodes.supervisor import supervisor_node
from .nodes.await_input import await_input_node, await_approval_node
from .nodes.save import make_save_node
from .nodes.verification import make_verification_node
from .nodes.condition_check import make_condition_check, audit_conditions
from .condition_templates import template_node
from .tool_gap import tool_gap_node
from .nodes.capability_check import make_capability_check, await_capability_node
from .capability_gate import generation_gate, require_ready

def _timing_event(event):
    path = os.getenv('A4B_TIMING_LOG')
    if not path:
        return
    try:
        with open(path, 'a', encoding='utf-8') as out:
            out.write(json.dumps(event, ensure_ascii=False) + '\n')
    except OSError:
        pass

def _timed_node(name, fn):
    """Attach elapsed time to completed synchronous stages without logging input data."""
    def run(state):
        started = time.monotonic()
        _timing_event({'event':'start','stage':name,'at':time.time()})
        try:
            result = fn(state)
        except Exception as error:
            _timing_event({'event':'error','stage':name,'at':time.time(),
                           'elapsed_ms':round((time.monotonic()-started)*1000,2),
                           'error_type':type(error).__name__})
            raise
        _timing_event({'event':'end','stage':name,'at':time.time(),
                       'elapsed_ms':round((time.monotonic()-started)*1000,2)})
        if not isinstance(result, dict):
            return result
        trace = list(result.get('trace', state.get('trace', [])))
        trace.append({'node': name, 'label': '단계 소요 시간', 'status': 'completed',
                      'detail': name, 'elapsed_ms': round((time.monotonic() - started) * 1000, 2)})
        return {**result, 'trace': trace}
    return run

def build_graph(storage,api_key='',model='gpt-4.1-mini',conversation=None,verifier=None,verifier_mode=None,condition_transport=None,condition_mode=None,capability_transport=None,capability_mode=None):
    connection=sqlite3.connect(storage.path,check_same_thread=False,timeout=10)
    saver=SqliteSaver(connection)
    graph=StateGraph(BuilderState)
    def available_resources(state):
        from .reusable_workflows import catalog
        return {**resources_node(state),'saved_workflows':catalog(storage,state.get('model',model),state.get('credential_id',''))}
    graph.add_node('resources',_timed_node('resources',available_resources))
    graph.add_node('capability_check',_timed_node('capability_check',make_capability_check(model,capability_transport,capability_mode)))
    graph.add_node('await_capability',await_capability_node)
    graph.add_node('generation_gate',generation_gate)
    graph.add_node('condition_check',_timed_node('condition_check',make_condition_check(api_key,model,condition_transport,condition_mode)))
    planner=conversation or make_conversation(api_key,model)
    def guarded_plan(state):
        # A restored checkpoint can resume directly at this node, bypassing edges.
        require_ready(state)
        from .condition_readiness import literal_condition_issues, explicit_policies, source_catalog
        from copy import deepcopy
        if literal_condition_issues(state):
            ledger=deepcopy(state['condition_ledger'])
            literals=explicit_policies(state,source_catalog(state)[0])
            for key,item in literals.items():
                old=ledger.get('slots',{}).get(key)
                if old and (old.get('status')!=item['status'] or old.get('value')!=item.get('value')):
                    if item['status']!='specified':
                        raise ValueError('이전 조건이 사용자 원문과 충돌합니다. 조건을 다시 확인해주세요.')
                    ledger['slots'][key]=item
            refreshed={**state,'condition_ledger':ledger,'confirmed':False}
            return {**planner(refreshed),'condition_ledger':ledger,'confirmed':False}
        return planner(state)
    def guarded_template(state):
        require_ready(state)
        return template_node(state)
    graph.add_node('template_plan',guarded_template)
    graph.add_node('conversation',_timed_node('conversation',guarded_plan))
    graph.add_node('condition_audit',_timed_node('condition_audit',audit_conditions))
    graph.add_node('validate',_timed_node('validate',validate_node))
    graph.add_node('verification',_timed_node('verification',make_verification_node(storage,verifier,verifier_mode)))
    graph.add_node('supervisor',_timed_node('supervisor',supervisor_node))
    graph.add_node('await_input',await_input_node)
    graph.add_node('await_approval',await_approval_node)
    graph.add_node('save',make_save_node(storage))
    graph.add_node('tool_gap',tool_gap_node)
    graph.add_edge(START,'resources');graph.add_edge('resources','capability_check')
    graph.add_conditional_edges('capability_check',lambda s:s['capability_route'],{'conditions':'condition_check','supervisor':'supervisor','stop':END,'await_capability':'await_capability'})
    graph.add_edge('await_capability','capability_check')
    graph.add_conditional_edges('generation_gate',lambda s:s['capability_generation_route'],{'generate':'conversation','recheck':'capability_check'})
    graph.add_conditional_edges('condition_check',lambda s:s['condition_route'],{'conversation':'generation_gate','template_plan':'template_plan','supervisor':'supervisor','stop':END})
    graph.add_conditional_edges('template_plan',lambda s:s['condition_route'],{'audit':'condition_audit','stop':END})
    graph.add_edge('conversation','condition_audit');graph.add_edge('condition_audit','validate');graph.add_edge('validate','verification');graph.add_edge('verification','supervisor')
    graph.add_conditional_edges('supervisor',lambda s:s['route'],{'unsupported':'tool_gap','invalid':END,'repair':'generation_gate','await_input':'await_input','await_approval':'await_approval','save':'save'})
    graph.add_edge('tool_gap',END)
    graph.add_edge('await_input','capability_check')
    graph.add_conditional_edges('await_approval',lambda s:'save' if s['confirmed'] else 'capability_check',{'save':'save','capability_check':'capability_check'})
    graph.add_edge('save',END)
    return graph.compile(checkpointer=saver),connection
