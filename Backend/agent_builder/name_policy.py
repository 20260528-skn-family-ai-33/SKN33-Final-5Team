"""Keep planner helper names out of saved user-facing workflow names."""

PLANNER_HELPERS=frozenset({'inspect_resource','list_tools','validate_workflow','inspect_inputs'})


def invalid_workflow_name(name):
    from .registry import TOOLS
    reserved=PLANNER_HELPERS | {item['id'].casefold() for item in TOOLS}
    return isinstance(name,str) and name.strip().casefold() in reserved


def grounded_fallback_name(name, messages):
    """Use the user's own request as a display name only when no name was requested.

    Never replace a legitimate model name, guess an explicitly requested name,
    inspect document contents, or change workflow settings. The caller must
    still validate the complete draft.
    """
    if not invalid_workflow_name(name) or not isinstance(messages, list):
        return None
    user_texts = [m.get('content', '') for m in messages if isinstance(m, dict) and m.get('role') == 'user']
    if not user_texts or any(not isinstance(text, str) for text in user_texts):
        return None
    # Be conservative: any mention of a name may carry a user-specified value.
    if any('이름' in text or '명칭' in text or 'name' in text.casefold() for text in user_texts):
        return None
    clean = ' '.join(user_texts[-1].split())
    return ('요청: ' + clean[:54]) if clean else None
