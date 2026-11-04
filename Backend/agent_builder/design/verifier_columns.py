"""Recheck a narrow set of legacy column findings against native execution.

The delivered verifier and its raw verdict remain unchanged. Only findings
proved false by a fully known native table chain can be replaced. Unknown
operations, invalid workflows and model/evidence failures never become passes.
"""
from copy import deepcopy
import re

from verification.workflow.contracts import CodeFinding, VerificationOutput
from verification.workflow.validation import choose_route
from workflow_engine.catalog.registry import uses_operation
from workflow_engine.table_formula import formula_columns
from workflow_engine.workflow import workflow_errors


def _native_lineage(draft, resources):
    workflow=draft.get('workflow') or {}
    if workflow_errors(workflow,draft.get('tool_ids',[]),resources,draft.get('execution_plan')):
        return {}
    if any(e.get('branch') in ('yes','no') for e in workflow.get('edges',[])):
        return {}
    nodes={n['id']:n for n in workflow.get('nodes',[])}
    tables={a['id']:a for a in resources if a.get('kind')=='table'}
    cache={};visiting=set()

    def columns(identifier):
        if identifier in cache:return cache[identifier]
        if identifier in visiting or identifier not in nodes:return None
        visiting.add(identifier)
        node=nodes[identifier];settings=node['settings'];result=None
        if not uses_operation(node):
            if node['tool']=='read_table':
                ids=settings.get('file_ids') or [i for i in draft.get('document_ids',[]) if i in tables]
                if len(ids)==1 and ids[0] in tables:
                    asset=tables[ids[0]]
                    aliases={'completed':'ready','succeeded':'ready','error':'failed'}
                    statuses=[aliases.get(asset.get(k,'ready'),asset.get(k,'ready'))
                              for k in ('processing_status','parser_status')]
                    if all(status=='ready' for status in statuses):
                        result=(set(asset['columns']),False)
            else:
                bindings=[b for b in node['bindings'] if b['input']=='table' and b['output']=='table']
                incoming=columns(bindings[0]['node_id']) if len(bindings)==1 else None
                if incoming:
                    fields,derived=incoming
                    fields=set(fields)
                    if node['tool']=='calculate_columns':
                        operation=settings.get('table_operation')
                        if operation=='compute':
                            output=settings.get('output_column')
                            if (output and output not in fields
                                    and formula_columns(settings.get('calculation_formula'))<=fields):
                                result=(fields|{output},True)
                        elif operation=='rename':
                            before=settings.get('rename_from');after=settings.get('rename_to')
                            if before in fields and after and (after==before or after not in fields):
                                result=((fields-{before})|{after},True)
                        elif operation=='parse_dates' and set(settings.get('fields',[]))<=fields:
                            result=(fields,derived)
                    elif node['tool']=='filter_sort':
                        plan=settings.get('execution_plan') or draft.get('execution_plan') or {}
                        used={f['field'] for f in plan.get('filters',[])}
                        used|={f['compare_field'] for f in plan.get('filters',[]) if f.get('compare_field')}
                        used|={s['field'] for s in plan.get('sort',[])}
                        if plan.get('duplicate_field'):used.add(plan['duplicate_field'])
                        if used<=fields:result=(fields,derived)
                    elif node['tool']=='aggregate_table':
                        groups=set(settings.get('fields',[]));aggregation=settings.get('aggregation') or 'count'
                        output='sum' if aggregation=='sum' else 'count'
                        if (groups<=fields and output not in groups and aggregation in ('sum','count')
                                and (aggregation=='count' or settings.get('sum_field') in fields)):
                            result=(groups|{output},derived)
                    elif node['tool']=='write_result':result=(fields,derived)
        visiting.remove(identifier);cache[identifier]=result
        return result

    for identifier in nodes:columns(identifier)
    return cache


def reconcile_column_findings(payload, draft, resources, result):
    """Retain every raw finding and record the proof used for scoped rechecks."""
    if (not payload or not draft or not draft.get('workflow') or result.get('error_code')
            or not result.get('model_output')):
        return result
    try:
        lineage=_native_lineage(draft,resources)
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        return result
    nodes=draft['workflow']['nodes'];retained=[];rechecked=[]
    for finding in result.get('findings',[]):
        match=re.fullmatch(r'/workflow/nodes/(0|[1-9][0-9]*)',finding.get('path') or '')
        replace=False
        if match and finding.get('severity')=='block' and int(match[1])<len(nodes):
            node=nodes[int(match[1])];proof=lineage.get(node['id']);code=finding.get('code')
            if proof and proof[1] and not uses_operation(node):
                replace=(node['tool']=='filter_sort' and code in ('unknown_column','unknown_sort_column')
                         or node['tool']=='aggregate_table' and code in ('missing_sum_field','invalid_aggregate_column'))
                if replace:
                    rechecked.append(dict(finding=deepcopy(finding),node_id=node['id'],
                                          verified_output_columns=sorted(proof[0]),
                                          basis='native execution settings and known upstream resource columns'))
        if not replace:retained.append(deepcopy(finding))
    if not rechecked:return result
    updated=deepcopy(result)
    updated['findings']=retained
    updated['route']=choose_route(payload,VerificationOutput.model_validate(result['model_output']),
                                   [CodeFinding.model_validate(f) for f in retained])
    updated['execution_allowed']=False
    updated['column_lineage_review']=dict(original_route=result['route'],
                                          original_findings=deepcopy(result['findings']),rechecked=rechecked)
    return updated


def reconcile_stored_column_findings(payload, result):
    """Use the exact recorded verifier snapshot in both live and deferred paths."""
    if payload.candidate_draft is None:return result
    from contracts.models import NodeSettings
    from agent_builder.design.native_settings import expand_node_settings
    try:
        draft=expand_node_settings(payload.candidate_draft.model_dump(mode='json'))
        for node in draft['workflow']['nodes']:
            node['settings']=NodeSettings.model_validate(node['settings']).model_dump(mode='json')
        draft['tool_ids']=list(dict.fromkeys(n['tool'] for n in draft['workflow']['nodes']))
        resources=[dict(id=d.id,kind='table',columns=d.tables[0].columns,
                        processing_status=d.parse_status,parser_status=d.parse_status)
                   for d in payload.documents if len(d.tables)==1]
        return reconcile_column_findings(payload,draft,resources,result)
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        return result
