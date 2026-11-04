"""Present run answers without serializing the file reference served by the download button."""
import json


def public_run_answer(answer, outputs):
    # Canonical operations expose their result as text as well as typed ports.
    # That text is needed by downstream nodes; change only the displayed answer.
    if not isinstance(answer, str) or '"path"' not in answer:
        return answer
    sections = answer.split('\n\n')
    replacements = {}
    for output in outputs.values():
        asset = output.get('asset')
        text = output.get('text')
        result = output.get('result')
        if (not output.get('artifact') or not isinstance(asset, dict) or 'path' not in asset
                or not isinstance(result, dict) or not isinstance(text, str) or text not in sections):
            continue
        if result != asset and result.get('artifact') != asset:
            continue
        try:
            # A document or an explicit answer can override the operation's default text port.
            if json.loads(text) != result:
                continue
        except ValueError:
            continue
        details = {key:value for key,value in asset.items() if key not in ('path','version','bytes')}
        if result == asset:
            visible = details
        else:
            visible = {key:value for key,value in result.items() if key != 'artifact'}
            if details:
                visible['artifact'] = details
        replacements[text] = json.dumps(visible, ensure_ascii=False, indent=2) if visible else None
    parts = (replacements.get(section, section) for section in sections)
    return '\n\n'.join(section for section in parts if section is not None)
