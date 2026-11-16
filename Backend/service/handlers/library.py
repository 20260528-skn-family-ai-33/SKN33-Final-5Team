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
    return {'agents':c.storage.agents(),'organization':organization(c.storage)}

def workflow_folders(c):
    from infrastructure.persistence.workflow_folders import change
    return {'organization':change(c.storage,c.data)}

def schedules(c):
    reservations=Schedules(c.storage).listing()
    return {'workflows':[{'agent':agent,'schedule':reservation}
                         for agent in c.storage.agents() if (reservation:=reservations.get(agent['id'])) is not None]}

def rename_agent(c):
    data=c.data
    return {'agent':c.storage.rename_agent(data['id'],data['name'],data['base_version'])}

def agent(c):
    data=c.data;storage=c.storage
    agent=storage.agent(data['id']);schedules=Schedules(storage)
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
    agents=[{'id':agent['id'],'version':agent['version'],'model':agent.get('model',''),
             'updated_at':agent.get('updated_at',''),'config':{'name':agent['config']['name'],
             'purpose':agent['config'].get('purpose',''),'tool_ids':[]}}
            for agent in c.storage.agents() if not selected or agent['id']==selected]
    by_id={agent['id']:agent for agent in agents}
    rows=[{'agent':by_id[agent_id],'run':run} for agent_id,run in c.storage.run_history(selected) if agent_id in by_id]
    from datetime import datetime, timezone
    def started(row):
        value=row['run'].get('started_at') or row['run'].get('created_at') or ''
        try:
            date=datetime.fromisoformat(value.replace('Z','+00:00'))
            return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError,TypeError):return 0
    rows.sort(key=started,reverse=True)
    return {'agents':agents,'rows':rows}
