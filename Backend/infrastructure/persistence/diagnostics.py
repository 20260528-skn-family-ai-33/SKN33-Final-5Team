"""Bounded diagnostic metadata: no prompts, input values or provider response bodies."""
import os
import re


def token(value, limit=120):
    return re.sub(r'[^a-zA-Z0-9_.:/-]', '', str(value if value is not None else ''))[:limit] or None


def diagnostic_metadata(error=None, *, trace_id=None, model=None):
    result = {'trace_id': token(trace_id), 'model': token(model or os.getenv('A4B_MAIN_MODEL')),
              'release': token(os.getenv('A4B_RELEASE')), 'validation': []}
    if error is None:
        return result
    code = getattr(error, 'status_code', None)
    if isinstance(code, int) and 100 <= code <= 599:
        result['http_status'] = code
    # Pydantic errors contain input/context and sometimes provider text. Keep only
    # structural locations and error types; never str(error), input, ctx or msg.
    errors = getattr(error, 'errors', None)
    if callable(errors):
        try:
            entries = errors(include_input=False, include_context=False, include_url=False)
            for entry in entries[:20]:
                result['validation'].append({'field': '.'.join(token(part,40) or '?' for part in entry.get('loc',[])[:8]),
                                             'type': token(entry.get('type'))})
        except Exception:
            pass
    return result
