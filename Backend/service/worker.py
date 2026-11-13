"""JSON over stdin/stdout bridge. Secrets are supplied only in process memory."""
import json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service.dispatch import handle
from infrastructure.llm.errors import public_error

__all__=['handle']

if __name__=='__main__':
    data={}
    # Node exchanges UTF-8 JSON. Windows pipes otherwise use the locale code page (e.g. cp949).
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    try:
        data=json.load(sys.stdin)
        if not isinstance(data,dict):raise ValueError('요청 형식을 확인하세요.')
        # Never enable remote tracing for API credentials or private documents.
        import os
        os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
        result=handle(data)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False))
    except Exception as error:
        import traceback,os
        if os.environ.get('A4B_DIAGNOSTICS'):
            Path(__import__('tempfile').gettempdir(),'a4b-worker-diagnostic.json').write_text(json.dumps({'type':type(error).__name__,'code':getattr(error,'code',None),'param':getattr(error,'param',None),'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-6:]]}))
        print(json.dumps({'ok':False,'error':public_error(error,data.get('api_key',''),data.get('gmail_access_token',''),data.get('slack_access_token',''))},ensure_ascii=False))
