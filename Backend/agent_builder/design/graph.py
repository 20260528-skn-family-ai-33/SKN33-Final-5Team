import time
import json
import os
from langgraph.graph import StateGraph, START, END
from langgraph.errors import GraphBubbleUp
from infrastructure.persistence.checkpoints import checkpoint_connection, checkpointer
from contracts.models import BuilderState
from agent_builder.design.nodes.resources import resources_node
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.supervisor import supervisor_node
from agent_builder.design.nodes.await_input import await_input_node, await_approval_node
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.verification import make_verification_node
from agent_builder.conditions.condition_check import make_condition_check, audit_conditions
from agent_builder.design.condition_templates import template_node
from agent_builder.tool_gap.gap import tool_gap_node
from agent_builder.capabilities.capability_check import make_capability_check, await_capability_node
from agent_builder.capabilities.capability_gate import generation_gate, require_ready

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

def build_graph(storage,api_key='',model='gpt-4.1-mini',conversation=None,verifier=None,verifier_mode=None,condition_transport=None,condition_mode=None,capability_transport=None,capability_mode=None,requirement_transport=None,requirement_mode=None):
    connection=checkpoint_connection(storage)
    saver=checkpointer(connection)
    graph=StateGraph(BuilderState)
    def observed_error(error):
        audit=getattr(storage,'generation_audit',None)
        if audit:audit.error(error)
    def add(name,fn):
        def observed(state):
            # Server-selected connection metadata is refreshed on every request,
            # including resumed interrupts. Never retain OAuth tokens in graph state.
            connected=getattr(storage,'gmail_connected',None)
            if hasattr(storage,'gmail_connected'):state={**state,'gmail_connected':connected if type(connected) is bool else None}
            audit=getattr(storage,'generation_audit',None)
            if audit:audit.enter(name)
            try:
                result=fn(state)
            except GraphBubbleUp:
                # LangGraph interrupts request user input/approval; they are control flow.
                raise
            except Exception as error:
                if audit:audit.error(error)
                raise
            if audit and isinstance(result,dict) and ('status' in result or name=='verification'):
                audit.observed(name,{**state,**result})
            return result
        graph.add_node(name,observed)
    def available_resources(state):
        from workflow_engine.library.reusable_workflows import catalog
        return {**resources_node(state),'saved_workflows':catalog(storage,state.get('model',model),state.get('credential_id',''))}
    add('resources',_timed_node('resources',available_resources))
    def quick_reply(state):
        # Code-only replies that must come before the capability model: a missing upload, or a
        # cancellation of the document fixed flow's field question (see document_rows_cancel_reply).
        from agent_builder.design.attachment_precheck import missing_attachment_reply
        from agent_builder.design.document_rows_template import document_rows_cancel_reply
        from agent_builder.design.receipt_intake import receipt_intake
        return receipt_intake(state) or missing_attachment_reply(state) or document_rows_cancel_reply(state)
    def attachment_gate(state):
        quick=quick_reply(state)
        if quick:
            return {'status':'needs_clarification','issues':[],'semantic_issues':[],'pending_questions':[],
                    'condition_enforced':False,'attachment_route':'ask',**quick}
        if any(q.get('source')=='receipt' for q in state.get('pending_questions',[])):
            from agent_builder.design.receipt_workbook import receipt_workbook
            workbook=receipt_workbook(state)
            if workbook:
                return {**workbook, 'attachment_route':'validate'}
            return {'pending_questions':[],'attachment_route':'continue'}
        return {'attachment_route':'continue'}
    add('attachment_gate',attachment_gate)
    graph.add_conditional_edges('attachment_gate',lambda s:s['attachment_route'],{'ask':'await_input','continue':'capability_check','validate':'validate'})
    add('capability_check',_timed_node('capability_check',make_capability_check(model,capability_transport,capability_mode,error_observer=observed_error)))
    add('await_capability',await_capability_node)
    add('generation_gate',generation_gate)
    add('condition_check',_timed_node('condition_check',make_condition_check(api_key,model,condition_transport,condition_mode,error_observer=observed_error)))
    from agent_builder.design.requirement_ledger import make_requirement_check
    add('requirements',_timed_node('requirements',make_requirement_check(model,requirement_transport,requirement_mode,api_key=api_key,error_observer=observed_error)))
    planner=conversation or make_conversation(api_key,model)
    def guarded_plan(state):
        # A restored checkpoint can resume directly at this node, bypassing edges.
        require_ready(state)
        from agent_builder.design.attachment_precheck import missing_attachment_reply
        quick=missing_attachment_reply(state)
        if quick:return quick
        from agent_builder.design.gmail_summary import gmail_summary_reply
        gmail=gmail_summary_reply(state)
        if gmail:return gmail
        from agent_builder.design.document_rows_template import document_rows_reply
        fixed=document_rows_reply(state)
        if fixed:return fixed
        from agent_builder.conditions.condition_readiness import literal_condition_issues, explicit_policies, source_catalog
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
    add('template_plan',guarded_template)
    add('conversation',_timed_node('conversation',guarded_plan))
    add('condition_audit',_timed_node('condition_audit',audit_conditions))
    add('validate',_timed_node('validate',validate_node))
    add('verification',_timed_node('verification',make_verification_node(storage,verifier,verifier_mode)))
    add('supervisor',_timed_node('supervisor',supervisor_node))
    add('await_input',await_input_node)
    add('await_approval',await_approval_node)
    add('save',make_save_node(storage))
    add('tool_gap',tool_gap_node)
    graph.add_edge(START,'resources');graph.add_edge('resources','attachment_gate')
    graph.add_conditional_edges('capability_check',lambda s:s['capability_route'],{'conditions':'condition_check','supervisor':'supervisor','stop':END,'await_capability':'await_capability'})
    graph.add_edge('await_capability','attachment_gate')
    graph.add_conditional_edges('generation_gate',lambda s:s['capability_generation_route'],{'generate':'conversation','recheck':'capability_check'})
    graph.add_conditional_edges('condition_check',lambda s:s['condition_route'],{'conversation':'requirements','template_plan':'requirements','supervisor':'supervisor','stop':END})
    graph.add_conditional_edges('requirements',lambda s:s['requirement_route'],{'plan':'generation_gate','template':'template_plan','ask':'await_input','stop':END})
    graph.add_conditional_edges('template_plan',lambda s:s['condition_route'],{'audit':'condition_audit','stop':END})
    graph.add_edge('conversation','condition_audit');graph.add_edge('condition_audit','validate');graph.add_edge('validate','verification');graph.add_edge('verification','supervisor')
    graph.add_conditional_edges('supervisor',lambda s:s['route'],{'unsupported':'tool_gap','invalid':END,'repair':'generation_gate','await_input':'await_input','await_approval':'await_approval','save':'save'})
    graph.add_edge('tool_gap',END)
    graph.add_edge('await_input','attachment_gate')
    graph.add_conditional_edges('await_approval',lambda s:'save' if s['confirmed'] else 'capability_check',{'save':'save','capability_check':'attachment_gate'})
    graph.add_edge('save',END)
    return graph.compile(checkpointer=saver),connection
