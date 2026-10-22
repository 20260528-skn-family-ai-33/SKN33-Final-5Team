"""DB/파서/메인 모델을 공통 계약에 연결하는 최소 어댑터."""
import json,sqlite3
from pathlib import Path
from .contracts import Document,Draft,ToolSpec,VerificationInput,VerificationResult

def load_registry():
    raw=json.loads(Path(__file__).with_name('registry.json').read_text(encoding='utf-8'))
    return [ToolSpec.model_validate(x) for x in raw['tools']]

def document_from_parser(normalized_payload):
    """제품별 파서 결과를 문서 계약 형태로 매핑한 뒤 이 함수를 호출한다."""
    return Document.model_validate(normalized_payload)

def draft_from_backend(raw,*,draft_id,version):
    """agent_builder.models.AgentDraft의 필요한 필드만 명시적으로 투영.

    다른 메인 모델 포맷은 별도 매핑이 필요하다. 원문 초안은 호출 측에서 보관한다.
    """
    if not raw.get('workflow'):raise ValueError('검증할 workflow가 없습니다.')
    document_ids=raw.get('document_ids',[]);nodes=[]
    for n in raw['workflow']['nodes']:
        settings=dict(n['settings'])
        if n['tool']=='filter_sort' and not settings.get('execution_plan'):
            settings['execution_plan']=raw.get('execution_plan')
        if n['tool'] in ['read_table','read_document'] and not settings.get('file_ids'):
            settings['file_ids']=document_ids
        nodes.append({'id':n['id'],'tool':n['tool'],'bindings':n.get('bindings',[]),'settings':settings})
    return Draft.model_validate({'id':draft_id,'version':version,'document_ids':document_ids,
        'workflow':{'entry':raw['workflow']['entry'],'nodes':nodes,'edges':raw['workflow']['edges']}})

class SQLiteStore:
    """로컬 연결 예제. 운영 DB migration/인증/보관 정책은 팀 DB 계층에서 구현."""
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS verifier_documents(document_id TEXT,version TEXT,payload TEXT NOT NULL,PRIMARY KEY(document_id,version))')
        self.db.execute('CREATE TABLE IF NOT EXISTS verifier_runs(request_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        self.db.commit()
    def save_document(self,doc:Document):
        payload=doc.model_dump_json();old=self.db.execute('SELECT payload FROM verifier_documents WHERE document_id=? AND version=?',(doc.id,doc.version)).fetchone()
        if old and old[0]!=payload:raise ValueError('같은 문서 버전의 내용은 덮어쓸 수 없습니다. 새 버전을 사용하세요.')
        with self.db:self.db.execute('INSERT OR IGNORE INTO verifier_documents VALUES(?,?,?)',(doc.id,doc.version,payload))
    def load_document(self,document_id,version):
        row=self.db.execute('SELECT payload FROM verifier_documents WHERE document_id=? AND version=?',(document_id,version)).fetchone()
        if row is None:raise KeyError('요청한 문서 버전이 DB에 없습니다.')
        return Document.model_validate_json(row[0])
    def save_result(self,result:VerificationResult):
        with self.db:self.db.execute('INSERT INTO verifier_runs VALUES(?,?)',(result.request_id,result.model_dump_json()))
    def close(self):self.db.close()

def repository_roundtrip(data:VerificationInput,store:SQLiteStore):
    """파서 정규화 → DB 저장·동일 버전 조회 → 검증 입력. 원본 객체 불변."""
    docs=[]
    for document in data.documents:
        parsed=document_from_parser(document.model_dump(mode='json'))
        store.save_document(parsed);docs.append(store.load_document(parsed.id,parsed.version))
    return VerificationInput.model_validate({**data.model_dump(mode='json'),'documents':[d.model_dump(mode='json') for d in docs]})
