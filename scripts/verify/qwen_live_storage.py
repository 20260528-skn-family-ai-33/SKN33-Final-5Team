"""Fatal, bounded evaluation snapshot persistence. Never becomes a model fallback."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


class InstrumentationFatal(BaseException):
    def __init__(self,diagnostic):
        super().__init__('Evaluation instrumentation failed; inference stopped')
        self.diagnostic=diagnostic


def instrumentation_failure(operation,error,**details):
    diagnostic={'event':'evaluation_instrumentation_failed','operation':operation,
                'error_type':type(error).__name__,'errno':getattr(error,'errno',None),
                'winerror':getattr(error,'winerror',None),**details}
    # Error messages, paths, prompts and credential values are deliberately omitted.
    try:print(json.dumps(diagnostic),file=sys.stderr,flush=True)
    except Exception as stderr_error:
        diagnostic['stderr_error_type']=type(stderr_error).__name__
    return InstrumentationFatal(diagnostic)


def write_report(path,report,*,replace_attempts=5,retry_delay=0.05):
    temporary=None;attempt=0;serialized=False
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix=path.name+'.',suffix='.writing',delete=False) as output:
            temporary=Path(output.name)
            json.dump(report,output,ensure_ascii=False,indent=2);output.write('\n')
        serialized=True
        for attempt in range(1,replace_attempts+1):
            try:
                os.replace(temporary,path)
                return
            except PermissionError as error:
                sharing_error=getattr(error,'winerror',None) in (None,5,32,33)
                if not sharing_error or attempt==replace_attempts:raise
                time.sleep(retry_delay*(2**(attempt-1)))
    except Exception as error:
        raise instrumentation_failure('atomic_snapshot_write',error,replace_attempts=attempt,
                recovery_complete=serialized,recovery_file=temporary.name if serialized else None) from None
    finally:
        # Preserve a fully serialized snapshot if replacement failed: it may hold
        # the only retained server response. Remove only incomplete staging data.
        if temporary is not None and not serialized:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
