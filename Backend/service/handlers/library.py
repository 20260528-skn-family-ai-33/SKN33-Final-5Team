"""Catalog, reusable node library and saved agent lookups."""
from workflow_engine.catalog.registry import TOOLS
from workflow_engine.scheduling.schedules import Schedules
from agent_builder.conditions.input_requirements import execution_requirements, execution_request_required
from documents.resources.document_resources import available_inputs


def nodes(c):
    from workflow_engine.library.node_library import list_nodes, default_nodes
    return {'nodes':list_nodes(c.storage),'defaults':default_nodes()}

def save_node(c):
    from workflow_engine.library.node_library import save
    data=c.data
    return {'node':save(c.storage,data['spec'],data.get('id'),data.get('base_version',0))}

def catalog(c):return {'tools':TOOLS,'business_tool_count':56,'control_compatibility_count':6,'engine':{'conversation':'deepagents','routing':'langgraph','storage':c.storage.dialect}}

def agents(c):return {'agents':c.storage.agents()}

def rename_agent(c):
    data=c.data
    return {'agent':c.storage.rename_agent(data['id'],data['name'],data['base_version'])}

def agent(c):
    data=c.data;storage=c.storage
    agent=storage.agent(data['id']);schedules=Schedules(storage)
    source=schedules.source(data['id'])
    return {'agent':agent,'tests':storage.tests(data['id']),'resources':available_inputs(storage,agent['config']['document_ids']),'schedule':schedules.get(data['id']),'source':source,'jobs':schedules.jobs(data['id']),
            'input_requirements':execution_requirements(agent['config'],storage,[source['asset_id']] if source else [],agent.get('workflow_dependencies')),
            'request_required':execution_request_required(agent['config'],agent.get('workflow_dependencies'))}
