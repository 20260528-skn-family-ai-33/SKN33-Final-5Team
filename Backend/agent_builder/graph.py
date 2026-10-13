import sqlite3
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .models import BuilderState
from .nodes.resources import resources_node
from .nodes.conversation import make_conversation
from .nodes.validation import validate_node
from .nodes.supervisor import supervisor_node
from .nodes.await_input import await_input_node, await_approval_node
from .nodes.save import make_save_node

def build_graph(storage,api_key='',model='gpt-4.1-mini',conversation=None):
    connection=sqlite3.connect(storage.path,check_same_thread=False,timeout=10)
    saver=SqliteSaver(connection)
    graph=StateGraph(BuilderState)
    def available_resources(state):
        from .reusable_workflows import catalog
        return {**resources_node(state),'saved_workflows':catalog(storage,state.get('model',model),state.get('credential_id',''))}
    graph.add_node('resources',available_resources)
    graph.add_node('conversation',conversation or make_conversation(api_key,model))
    graph.add_node('validate',validate_node)
    graph.add_node('supervisor',supervisor_node)
    graph.add_node('await_input',await_input_node)
    graph.add_node('await_approval',await_approval_node)
    graph.add_node('save',make_save_node(storage))
    graph.add_edge(START,'resources');graph.add_edge('resources','conversation');graph.add_edge('conversation','validate');graph.add_edge('validate','supervisor')
    graph.add_conditional_edges('supervisor',lambda s:s['route'],{'unsupported':END,'invalid':END,'repair':'conversation','await_input':'await_input','await_approval':'await_approval','save':'save'})
    graph.add_edge('await_input','conversation')
    graph.add_conditional_edges('await_approval',lambda s:'save' if s['confirmed'] else 'conversation',{'save':'save','conversation':'conversation'})
    graph.add_edge('save',END)
    return graph.compile(checkpointer=saver),connection
