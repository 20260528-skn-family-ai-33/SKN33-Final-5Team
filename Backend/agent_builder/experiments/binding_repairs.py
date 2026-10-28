"""Conservative port spelling repairs, without choosing tools/data/topology.

Only an already-declared direct predecessor and a uniquely compatible output
may repair an unknown output name. Real outputs, ambiguous ports and missing
nodes are never rewritten. Every change is returned for audit.
"""
from copy import deepcopy

from ..registry import node_ports


def repair_bindings(workflow, resources):
    result = deepcopy(workflow)
    changes = []
    if not result:return result, changes
    nodes = result['nodes']
    by = {n['id']:n for n in nodes}
    if len(by) != len(nodes):return result, changes
    links = {(e['source'],e['target']) for e in result['edges']}
    available = {r['id']:r for r in resources}
    for node in nodes:
        ins, _ = node_ports(node)
        ids = node['settings']['file_ids']
        kind = {'read_table':'table','read_document':'document'}.get(node['tool'])
        can_read = kind and ids and all(i in available and available[i]['kind']==kind for i in ids)
        kept = []
        for binding in node['bindings']:
            if can_read and binding['node_id']==node['id']:
                changes.append({'node_id':node['id'],'before':deepcopy(binding),'after':None,
                                'rule':'reader_self_binding_with_explicit_valid_file_ids'})
                continue
            source = by.get(binding['node_id'])
            if source and source['id']!=node['id'] and (source['id'],node['id']) in links and binding['input'] in ins:
                _, outs = node_ports(source)
                if binding['output'] not in outs:
                    candidates = [name for name,kind in outs.items() if kind==ins[binding['input']]]
                    if len(candidates)==1:
                        before = deepcopy(binding)
                        binding['output'] = candidates[0]
                        changes.append({'node_id':node['id'],'before':before,'after':deepcopy(binding),
                                        'rule':'unique_typed_output_of_existing_direct_predecessor'})
            kept.append(binding)
        node['bindings'] = kept
    return result, changes
