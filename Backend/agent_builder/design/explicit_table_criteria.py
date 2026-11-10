"""Check explicit user-provided table criteria independently of model extraction.

Only the latest user message's labelled JSON is a source of authority. Uploaded
text, previous drafts and earlier overwritten user criteria are never parsed.
This supplements the existing natural-language guards; it does not infer missing
business values, rewrite a graph, or approve a model's claim of completeness.
"""
import json
from contracts.models import FilterCondition, SortCondition

MARKER='제공하는 실행 조건:'
ALLOWED={'missing_policy','duplicate_policy','duplicate_field','selection','match','sort',
         'aggregations','calculations','date_column','date_columns','date_input_format',
         'date_timezone','join','output_format','report_destination','output_columns'}

def explicit_criteria(state):
    users=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if not users or MARKER not in users[-1]:return None
    text=users[-1]
    if text.count(MARKER)!=1:raise ValueError('명시한 실행 조건 JSON이 여러 개라 확정할 수 없습니다.')
    tail=text.split(MARKER,1)[1].strip()
    if len(tail)>20000:raise ValueError('명시한 실행 조건이 너무 깁니다.')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('실행 조건에 중복 JSON 키가 있습니다: '+k)
            result[k]=v
        return result
    value=json.loads(tail,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('유한한 숫자가 필요합니다.')))
    if not isinstance(value,dict) or not set(value)<=ALLOWED:raise ValueError('명시한 실행 조건 JSON의 지원 항목을 확인하세요.')
    return value

def expected_specs(criteria):
    specs=[]
    if 'selection' in criteria:
        filters=[FilterCondition.model_validate(x).model_dump(mode='json') for x in criteria['selection']]
        # ExecutionPlan defaults multiple conditions to all, unless any is explicit.
        match=criteria.get('match','all')
        if match not in ('all','any'):raise ValueError('선별 조건의 match는 all/any여야 합니다.')
        if filters:specs.append(dict(kind='filter',filters=filters,match=match))
    if criteria.get('sort'):
        specs.append(dict(kind='sort',sort=[SortCondition.model_validate(x).model_dump(mode='json') for x in criteria['sort']]))
    for a in criteria.get('aggregations',[]):
        specs.append(dict(kind='aggregate',fields=a['groups'],aggregation=a['operation'],sum_field=a['value_column']))
    for c in criteria.get('calculations',[]):specs.append(dict(kind='compute',formula=c['formula'],output_column=c['output_column']))
    fields=criteria.get('date_columns') or ([criteria['date_column']] if criteria.get('date_column') else [])
    if fields:specs.append(dict(kind='parse_dates',fields=fields,input_format=criteria['date_input_format'],timezone=criteria['date_timezone']))
    if criteria.get('join'):
        join=criteria['join'];specs.append(dict(kind='join',**{k:join[k] for k in ('keys','how','cardinality','null_keys','max_rows')},left_sources=join.get('left_sources',[]),right_sources=join.get('right_sources',[])))
    if criteria.get('output_format'):specs.append(dict(kind='export',format=criteria['output_format']))
    if any(k in criteria for k in ('missing_policy','duplicate_policy')):
        specs.append(dict(kind='policy',**{k:criteria.get(k) for k in ('missing_policy','duplicate_policy','duplicate_field')}))
    # Validate against the same service spec union; unknown settings cannot vanish.
    from .requirement_contracts import RequiredTask
    for i,spec in enumerate(specs):
        RequiredTask.model_validate(dict(id='r'+str(i+1),source_message=0,quote='명시 조건',description='명시 조건',depends_on=[],spec=spec))
    return specs

def _spec_equal(actual,expected):
    from .requirement_coverage import _same_filters,_formula
    kind=expected['kind']
    if actual.get('kind')!=kind:return False
    if kind=='filter':return _same_filters(actual['filters'],expected['filters'],actual['match'],expected['match'])
    if kind=='compute':return actual['output_column']==expected['output_column'] and _formula(actual['formula'])==_formula(expected['formula'])
    if kind=='policy':return all(actual.get(k)==v for k,v in expected.items() if k!='kind' and v is not None)
    if kind=='join':return all(actual.get(k)==v for k,v in expected.items() if k not in ('left_sources','right_sources') or v)
    return actual==expected

def validate_explicit_ledger(plan,state):
    criteria=explicit_criteria(state)
    if criteria is None:return
    for expected in expected_specs(criteria):
        if not any(_spec_equal(t['spec'],expected) for t in plan['tasks']):
            raise ValueError('사용자가 명시한 '+expected['kind']+' 조건이 요청 업무 목록에서 누락되거나 변경됐습니다: '+json.dumps(expected,ensure_ascii=False))

def explicit_draft_issues(state,draft):
    try:
        criteria=explicit_criteria(state)
        if criteria is None or not draft.get('workflow'):return []
        specs=expected_specs(criteria)
        from .requirement_coverage import _matches
        from .content_lineage import content_ancestors
        from .reader_sources import reader_sources
        nodes={n['id']:n for n in draft['workflow']['nodes']}
        from workflow_engine.execution_path import artifact_output_ids,artifact_origin,execution_path
        outputs=artifact_output_ids(draft['workflow'])
        path=execution_path(draft['workflow'])
        if not path:raise ValueError('실행 가능한 시작 단계를 확인하지 못했습니다.')
        end=artifact_origin(draft['workflow'],outputs[-1]) if outputs else path[-1]['id']
        active=content_ancestors(nodes,end)|{end}
        def sources(id):
            result=set()
            for key in content_ancestors(nodes,id)|{id}:
                n=nodes[key]
                if n['tool']=='read_table':result.update(reader_sources(n,state.get('resources',[]),draft.get('document_ids',[])))
            return result
        issues=[]
        if 'selection' in criteria:
            from .requirement_coverage import _condition
            actual_filters=[]
            for id,n in nodes.items():
                if id in active and n['tool']=='filter_sort' and not n['settings'].get('operation'):
                    actual_filters.extend((n['settings'].get('execution_plan') or draft['execution_plan'])['filters'])
            expected_filters=[FilterCondition.model_validate(f).model_dump(mode='json') for f in criteria['selection']]
            if sorted(map(_condition,actual_filters))!=sorted(map(_condition,expected_filters)):
                issues.append('명시 조건 검증: 최종 출력 경로의 선별 조건이 누락되거나 요청하지 않은 조건이 추가됐습니다.')
        for spec in specs:
            if spec['kind']=='policy':
                policy_nodes=[n for id,n in nodes.items() if id in active and n['tool']=='filter_sort' and not n['settings'].get('operation')]
                if not policy_nodes:
                    policies=[spec[k] for k in ('missing_policy','duplicate_policy')]
                    keep_only='keep' in policies and all(value in (None,'keep') for value in policies)
                    # Native table reads/writes preserve nulls and repeated rows
                    # without a filter. Do not infer this for transformations or
                    # operations merely sharing those tool names.
                    unchanged=(active <= {n['id'] for n in path}
                               and any(nodes[id]['tool']=='read_table' for id in active)
                               and all(nodes[id]['tool'] in ('read_table','write_result')
                                       and not nodes[id]['settings'].get('operation') for id in active))
                    if not (keep_only and unchanged):
                        issues.append('명시 조건 검증: 요청한 빈값·중복 정책을 적용하는 실행 단계를 확인하지 못했습니다.')
                for key,value in spec.items():
                    if key=='kind' or value is None:continue
                    for n in policy_nodes:
                        effective=n['settings'].get('execution_plan') or draft['execution_plan']
                        if effective.get(key)!=value:issues.append('명시 조건 검증: '+n['id']+'의 '+key+' 설정이 사용자 요청과 다릅니다.')
                continue
            if not any(_matches(spec,n,draft,sources) for id,n in nodes.items() if id in active):
                issues.append('명시 조건 검증: '+spec['kind']+'의 실제 설정·최종 출력 경로가 사용자 요청과 다릅니다: '+json.dumps(spec,ensure_ascii=False))
        return issues
    except (ValueError,TypeError,KeyError,ArithmeticError) as error:
        return ['명시 조건 검증: 조건을 안전하게 대조하지 못했습니다. '+str(error)]
