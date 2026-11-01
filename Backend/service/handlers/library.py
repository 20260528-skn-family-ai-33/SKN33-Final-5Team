"""Catalog, reusable node library and saved agent lookups."""
from workflow_engine.catalog.registry import TOOLS
from workflow_engine.scheduling.schedules import Schedules
from agent_builder.conditions.input_requirements import execution_requirements


def nodes(c):
    from workflow_engine.library.node_library import list_nodes, default_nodes
    return {'nodes':list_nodes(c.storage),'defaults':default_nodes()}

def save_node(c):
    from workflow_engine.library.node_library import save
    data=c.data
    return {'node':save(c.storage,data['spec'],data.get('id'),data.get('base_version',0))}

def catalog(c):return {'tools':TOOLS,'engine':{'conversation':'deepagents','routing':'langgraph','storage':'sqlite'}}

def agents(c):return {'agents':c.storage.agents()}

def agent(c):
    data=c.data;storage=c.storage
    agent=storage.agent(data['id']);schedules=Schedules(storage)
    source=schedules.source(data['id'])
    return {'agent':agent,'tests':storage.tests(data['id']),'resources':[storage.asset(id) for id in agent['config']['document_ids']],'schedule':schedules.get(data['id']),'source':source,'jobs':schedules.jobs(data['id']),
            'input_requirements':execution_requirements(agent['config'],storage,[source['asset_id']] if source else [],agent.get('workflow_dependencies'))}
