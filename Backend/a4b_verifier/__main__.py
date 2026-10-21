import argparse,json,sys
from pathlib import Path
from .contracts import VerificationInput
from .client import OllamaConfig,health
from .service import verify
from .adapters import SQLiteStore,repository_roundtrip

def main():
    p=argparse.ArgumentParser(description='A4B 검증 연동 예제. 실제 업무를 실행하지 않습니다.')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('health',help='Ollama 서버와 로컬 모델 존재 확인')
    v=sub.add_parser('verify',help='입력 한 건 검증')
    v.add_argument('--input',required=True,type=Path);v.add_argument('--mode',choices=['fixture','live'],required=True)
    v.add_argument('--fixture',type=Path);v.add_argument('--db',type=Path);v.add_argument('--output',type=Path)
    a=p.parse_args();store=None
    try:
        if a.command=='health':print(json.dumps(health(OllamaConfig.from_env()),ensure_ascii=False,indent=2));return
        if a.input.stat().st_size>1_000_000:raise ValueError('입력 파일은 1MB 이하여야 합니다.')
        data=VerificationInput.model_validate_json(a.input.read_text())
        if a.db:
            store=SQLiteStore(a.db);data=repository_roundtrip(data,store)
        fixture=json.loads(a.fixture.read_text()) if a.fixture else None
        result=verify(data,mode=a.mode,fixture=fixture)
        if store:store.save_result(result)
        text=result.model_dump_json(indent=2)
        if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(text)
        print(text)
        if result.error_code:sys.exit(2)
    except (ValueError,KeyError,OSError,RuntimeError):
        print(json.dumps({'error':'입력·설정·서버 상태를 확인하세요. 검증이 완료되지 않았습니다.','execution_allowed':False},ensure_ascii=False),file=sys.stderr)
        sys.exit(2)
    finally:
        if store:store.close()
if __name__=='__main__':main()
