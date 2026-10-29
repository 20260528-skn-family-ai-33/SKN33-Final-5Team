"""Keep planner helper names out of saved user-facing workflow names."""

PLANNER_HELPERS=frozenset({'inspect_resource','list_tools','validate_workflow','inspect_inputs'})


def invalid_workflow_name(name):
    from .registry import TOOLS
    reserved=PLANNER_HELPERS | {item['id'].casefold() for item in TOOLS}
    return isinstance(name,str) and name.strip().casefold() in reserved
