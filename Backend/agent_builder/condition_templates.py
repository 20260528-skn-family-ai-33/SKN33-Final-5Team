"""Explicit experimental templates; not general natural-language planning."""
import re

from .models import AgentDraft, ExecutionPlan, NodeSettings, ScheduleSpec


def compile_conditions(state):
    ledger=state.get('condition_ledger') or {}
    if ledger.get('decision')!='ready' or len(ledger.get('operations',[]))!=1:
        raise ValueError('단일 대표 업무의 확정 조건이 필요합니다.')
    operation=ledger['operations'][0]
    slots={key:item['value'] for key,item in ledger['slots'].items() if item['status']=='specified'}
    text='\n'.join(m['content'] for m in state.get('messages',[]) if m['role']=='user')
    if re.search(r'오름차순|내림차순',text):
        raise ValueError('정렬이 포함된 요청은 이 대표 템플릿의 검증 범위 밖입니다.')
    plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy=slots.get('missing.policy','ask'),
        duplicate_policy=slots.get('duplicate.policy','keep'),duplicate_field=slots.get('duplicate.field'),
        output_format=slots.get('output.format','json')).model_dump()
    if operation=='filter':
        value=slots['filter.value']
        plan['filters']=[{'field':slots['filter.field'],'operator':slots['filter.operator'],'value':value,
            'compare_field':None,'value_type':'number' if isinstance(value,(int,float)) else 'text'}]
    fields=[key for key in [slots.get('filter.field'),slots.get('duplicate.field')] if key]
    tables=[r for r in state.get('resources',[]) if r.get('kind')=='table']
    candidates=[r for r in tables if all(field in r.get('columns',[]) for field in fields)]
    if tables and len(candidates)!=1:
        raise ValueError('대표 템플릿에 사용할 표 자료를 하나로 식별할 수 없습니다.')
    selected=[candidates[0]['id']] if candidates else []
    if operation=='schedule' and not selected:
        raise ValueError('예약할 관리 표 자료가 필요합니다.')
    nodes=[];edges=[]
    def add(identifier,tool,bindings=None,**settings):
        spec=NodeSettings(file_ids=[],query=None,fields=[],instructions=None,format=None,question=None,condition_field=None,condition_operator=None).model_dump()
        nodes.append({'id':identifier,'tool':tool,'label':{'read_table':'표 읽기','filter_sort':'확정 조건 적용','write_result':'결과 저장'}[tool],
                      'bindings':bindings or [],'settings':{**spec,**settings}})
        if len(nodes)>1:edges.append({'source':nodes[-2]['id'],'target':identifier,'branch':'next'})
    add('read','read_table',file_ids=selected)
    last='read'
    if operation in ('filter','deduplicate'):
        add('apply','filter_sort',[{'input':'table','node_id':last,'output':'table'}],execution_plan=plan)
        last='apply'
    if 'output.format' in slots or operation=='schedule':
        add('output','write_result',[{'input':'table','node_id':last,'output':'table'}],format=plan['output_format'])
    requirements=[]
    for item in ledger['slots'].values():
        for evidence in item.get('evidence',[]):
            source={'description':item['key'],'source_id':evidence['source_id'],'quote':evidence['quote']}
            if not any(r['source_id']==source['source_id'] and r['quote']==source['quote'] for r in requirements):requirements.append(source)
    schedule=None
    if operation=='schedule':
        schedule=ScheduleSpec(**{key:slots.get('schedule.'+key) for key in ['frequency','timezone','hour','minute','weekday']}).model_dump()
    draft=AgentDraft(name='확정 조건의 표 업무',purpose='검증한 조건을 등록 도구에 적용',
        instructions='확정된 조건과 정책을 적용합니다. '+str(slots),tool_ids=[n['tool'] for n in nodes],
        document_ids=[r['id'] for r in state.get('resources',[])],requirements=requirements,
        questions=[],question_options=[],unsupported=[],explanation='대표 업무 템플릿으로 확정 조건을 실행 설정에 반영했습니다. 확인 후 저장하세요.',
        execution_plan=plan,workflow={'entry':'read','nodes':nodes,'edges':edges},schedule=schedule,
        report_destination='reports' if schedule else None).model_dump()
    return {'draft':draft,'semantic_issues':[],'confirmed':False,'saved_agent_id':None,
            'trace':state.get('trace',[])+[{'node':'condition_template','label':'대표 표 업무 구성','status':'completed',
                'detail':'실험용 고정 구조에 확정 조건을 적용했습니다. 일반 LLM 그래프 생성 결과가 아닙니다.','model_calls':0}]}


def template_node(state):
    try:
        return {**compile_conditions(state),'condition_route':'audit'}
    except ValueError as error:
        return {'condition_route':'stop','status':'validation_failed','confirmed':False,'saved_agent_id':None,
                'issues':[str(error)],'messages':state.get('messages',[])+[{'role':'assistant','content':str(error)}],
                'trace':state.get('trace',[])+[{'node':'condition_template','label':'대표 표 업무 구성','status':'needs_input','detail':str(error),'model_calls':0}]}
