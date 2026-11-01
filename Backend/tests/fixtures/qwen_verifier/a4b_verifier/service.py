"""판정·코드 검사 결합. 실제 업무 실행 권한은 항상 False."""
import time
from pydantic import ValidationError
from .contracts import VerificationInput,VerificationOutput,VerificationResult,CodeFinding
from .client import OllamaConfig,call_qwen
from .validation import code_checks,output_checks,choose_route

def verify(data:VerificationInput,*,mode='live',fixture=None,config=None):
    if mode not in ['fixture','live']:raise ValueError('mode는 fixture 또는 live여야 합니다.')
    started=time.perf_counter();findings=code_checks(data)
    try:
        if any(f.code=='nonfinite_json' for f in findings):raise RuntimeError('invalid_json_number')
        if mode=='fixture':
            if fixture is None:raise ValueError('fixture 모드는 명시적인 예제 응답이 필요합니다.')
            out=VerificationOutput.model_validate(fixture)
        else:
            config=config or OllamaConfig.from_env();out,_=call_qwen(data,config)
        findings.extend(output_checks(data,out));route=choose_route(data,out,findings)
        return VerificationResult(request_id=data.request_id,model_output=out,findings=findings,route=route,
            mode=mode,model=config.model if mode=='live' else None,latency_ms=(time.perf_counter()-started)*1000)
    except (RuntimeError,ValueError,TypeError,KeyError) as exc:
        # Never invent a successful classification after timeout, invalid JSON or contract error.
        code=str(exc) if isinstance(exc,RuntimeError) else 'invalid_model_output'
        if len(code)>100:code='invalid_model_output'
        findings.append(CodeFinding(code=code,severity='review',detail='모델 호출 또는 응답 검증에 실패했습니다. 재검토가 필요합니다.'))
        return VerificationResult(request_id=data.request_id,model_output=None,findings=findings,route='manual_review',mode=mode,
            model=(config.model if config else None),latency_ms=(time.perf_counter()-started)*1000,error_code=code)
