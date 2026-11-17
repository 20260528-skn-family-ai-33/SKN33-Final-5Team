"""Catalog, reusable node library and saved agent lookups."""
from workflow_engine.catalog.registry import TOOLS
from workflow_engine.scheduling.schedules import Schedules, source_required, required_connections, scheduled_agent
from agent_builder.conditions.input_requirements import execution_requirements, execution_request_required
from documents.resources.document_resources import available_inputs
from workflow_engine.scheduling.library_sources import LibrarySources


def nodes(c):
    from workflow_engine.library.node_library import list_nodes, default_nodes
    return {'nodes':list_nodes(c.storage),'defaults':default_nodes()}

def save_node(c):
    from workflow_engine.library.node_library import save
    data=c.data
    return {'node':save(c.storage,data['spec'],data.get('id'),data.get('base_version',0))}

def catalog(c):return {'tools':TOOLS,'business_tool_count':56,'control_compatibility_count':6,'engine':{'conversation':'deepagents','routing':'langgraph','storage':c.storage.dialect}}

def agents(c):
    from infrastructure.persistence.workflow_folders import organization
    return {'agents':c.storage.agent_summaries() if c.data.get('summary') else c.storage.agents(),'organization':organization(c.storage)}

def workflow_folders(c):
    from infrastructure.persistence.workflow_folders import change
    return {'organization':change(c.storage,c.data)}

def schedules(c):
    reservations=Schedules(c.storage).listing()
    agents=c.storage.agents()
    result={'workflows':[{'agent':agent,'schedule':reservation}
                         for agent in agents if (reservation:=reservations.get(agent['id'])) is not None]}
    if getattr(c,'data',{}).get('include_agents'):
        result['agents']=[{'id':a['id'],'version':a['version'],'config':{'name':a['config']['name'],'purpose':a['config'].get('purpose','')}} for a in agents]
    return result

def rename_agent(c):
    data=c.data
    return {'agent':c.storage.rename_agent(data['id'],data['name'],data['base_version'])}

def agent(c):
    data=c.data;storage=c.storage
    agent=storage.agent(data['id'],include_deleted=True);schedules=Schedules(storage)
    if agent.get('deleted_at'):
        return {'agent':agent,'tests':storage.tests(data['id']),'resources':[],'schedule':None,'source':None,'library_source':None,'source_required':False,'required_connections':[],'jobs':[],'input_requirements':[],'request_required':False}
    reservation=schedules.get(data['id'])
    scheduled_config=scheduled_agent(agent,reservation['spec'] if reservation else {'timezone':'Asia/Seoul'})['config']
    source=schedules.execution_source(agent)
    return {'agent':agent,'tests':storage.tests(data['id']),'resources':available_inputs(storage,agent['config']['document_ids']),'schedule':reservation,'source':source,'library_source':LibrarySources(storage).get(data['id']),'source_required':bool(source) or source_required(agent['config'],storage,None,agent.get('workflow_dependencies')),'required_connections':required_connections(agent['config'],agent.get('workflow_dependencies')),'jobs':schedules.jobs(data['id']),
            'input_requirements':execution_requirements(agent['config'],storage,[source['asset_id']] if source else [],agent.get('workflow_dependencies')),
            'schedule_request_required':execution_request_required(scheduled_config,agent.get('workflow_dependencies')),
            'request_required':execution_request_required(agent['config'],agent.get('workflow_dependencies'))}


def history(c):
    # History needs names and run list fields, never documents, schedules or run outputs.
    selected=c.data.get('id')
    page=c.data.get('page',1)
    if type(page) is not int or not 1<=page<=10000:raise ValueError('실행 기록 페이지를 확인하세요.')
    page_size=50
    rows=[{'agent_id':agent_id,'run':run} for agent_id,run in c.storage.run_history(selected)]
    from datetime import datetime, timezone
    def started(row):
        value=row['run'].get('started_at') or row['run'].get('created_at') or ''
        try:
            date=datetime.fromisoformat(value.replace('Z','+00:00'))
            return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError,TypeError):return 0
    rows.sort(key=lambda row:(started(row),row['agent_id'],row['run']['id']),reverse=True)
    total=len(rows)
    rows=rows[(page-1)*page_size:page*page_size]
    ids={row['agent_id'] for row in rows}
    if selected:ids.add(selected)
    agents=c.storage.history_agents(sorted(ids))
    return {'agents':agents,'rows':rows,'total':total,'page':page,'page_size':page_size}


def studio(c):
    # A single worker/storage connection prepares the editor's shared metadata.
    return {'agents':c.storage.agent_summaries(),**nodes(c),**catalog(c)}


def delete_entry(c):
    from infrastructure.persistence.library_deletion import remove
    return remove(c.storage, c.data['kind'], c.data['id'], c.data['base_version'])
