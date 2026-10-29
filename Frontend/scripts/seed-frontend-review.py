"""Seed synthetic UI review fixtures in a fresh a4b-ui-* directory only.
Run from the repository root with PYTHONPATH=Backend and the agent Python runtime.
The credential ID must be created in the isolated review server, not a real account.
No LLM invocation or external request is performed here.
"""
import json,sys,hashlib
from pathlib import Path
from agent_builder.storage import Storage,uid
from agent_builder.models import AgentDraft
from agent_builder.test_builder import initial,draft
from agent_builder.test_workflow import node,edge,bind
from agent_builder.node_library import save as save_node
from agent_builder.scheduling import Schedules
folder=Path(sys.argv[1]);credential=sys.argv[2]
if not folder.name.startswith('a4b-ui-'):
    raise SystemExit('Use a separate temporary directory named a4b-ui-*. Never seed a user database.')
if (folder/'browser-fixture-ids.json').exists():
    raise SystemExit('Review fixtures already exist. Use a fresh review directory.')
st=Storage(folder)
if st.agents():
    st.close()
    raise SystemExit('Refusing to seed a database that already has workflows.')
a={'id':uid(),'name':'검증용_매출.csv','kind':'table','format':'csv','columns':['team','amount'],'row_count':3,'preview':[{'team':'A','amount':10},{'team':'A','amount':20},{'team':'B','amount':40}],'profile':{'team':{'types':['string'],'missing':0},'amount':{'types':['number'],'missing':0}},'text':'','hash':'browser-fixture','version':1}
st.add_asset(a,a['preview'])
ids={}
for name,scheduled,files in [('브라우저 검증 - 합계',False,[a['id']]),('브라우저 검증 - 예약',True,[a['id']]),('브라우저 검증 - 파일 필요',False,[])]:
 d=draft();d.update(name=name,instructions='team별 amount 합계를 JSON으로 저장',tool_ids=['read_table','aggregate_table','write_result'],document_ids=files,workflow={'entry':'read','nodes':[node('read','read_table'),node('sum','aggregate_table',[bind('table','read','table')],fields=['team'],aggregation='sum',sum_field='amount'),node('out','write_result',[bind('table','sum','table')],format='json')],'edges':[edge('read','sum'),edge('sum','out')]});d['execution_plan'].update(missing_policy='error',output_format='json')
 if scheduled:d.update(schedule={'frequency':'daily','timezone':'Asia/Seoul','hour':23,'minute':59,'weekday':None},report_destination='reports')
 s=initial();s.update(credential_id=credential,draft=AgentDraft.model_validate(d).model_dump(),confirmed=True);ids[name]=st.save_agent(s)
 if scheduled:Schedules(st).activate(ids[name],False)
# Keep a real complex node for preservation checks.
spec={'kind':'processing','name':'복합 필터 보존 검증','purpose':'두 조건과 정렬을 보존합니다.','tool':'filter_sort','instructions':'','reference':'','model':None,'settings':node('n','filter_sort')['settings'],'execution_plan':{'filters':[{'field':'amount','operator':'gte','value':10,'value_type':'number','compare_field':None},{'field':'amount','operator':'lt','value':100,'value_type':'number','compare_field':None}],'sort':[{'field':'amount','direction':'descending','value_type':'number'}],'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'json'}}
result=save_node(st,spec);ids['complex_node']=result['id']
st.close();(folder/'browser-fixture-ids.json').write_text(json.dumps(ids,ensure_ascii=False,indent=2));print(json.dumps(ids,ensure_ascii=False))
