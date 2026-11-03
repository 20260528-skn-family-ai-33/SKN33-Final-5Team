"""Keep planner helper names out of saved user-facing workflow names."""

PLANNER_HELPERS=frozenset({'inspect_resource','list_tools','validate_workflow','inspect_inputs'})


def invalid_workflow_name(name):
    from workflow_engine.catalog.registry import TOOLS
    reserved=PLANNER_HELPERS | {item['id'].casefold() for item in TOOLS}
    return isinstance(name,str) and name.strip().casefold() in reserved


def present_generated_name(draft,messages):
    """Use the generated business purpose for a missing display title only.

    Explicit naming requests and the validation/save guards remain unchanged.
    No tool, setting, evidence or execution order is edited.
    """
    import re
    if not draft.get('workflow') or not invalid_workflow_name(draft.get('name')):return draft
    users='\n'.join(m['content'] for m in messages if m['role']=='user')
    if re.search(r'(?:이름|제목)(?:은|을|는|으로|\s*:)|\b(?:name|title)\s*(?:is|as|:)',users,re.I):return draft
    purpose=' '.join((draft.get('purpose') or '').split())[:60].strip()
    if purpose and not invalid_workflow_name(purpose):return {**draft,'name':purpose}
    return draft
