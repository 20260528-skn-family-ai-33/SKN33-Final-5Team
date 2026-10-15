"""Junghyun reply diagnostics integrated into A4B's existing worker and model."""
from pydantic import BaseModel
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain.agents.structured_output import ToolStrategy
from langchain.agents.middleware import wrap_model_call
from deepagents import create_deep_agent
from .review_contracts import AnalysisReport, ResponseReport, ReviewReport, ReviewRequest, Evidence
from .review_prompts import COMMON, ROLE_PROMPTS

class ReviewExecutionError(ValueError):
    """A safe public error; raw provider request/response data stay private."""
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.message = public_message


def _validate_evidence(evidence: list[Evidence], request: ReviewRequest, accessed: set[str]) -> None:
    texts = {'customer': request.customer_message, 'policy': request.policy_text}
    tool_names = {'customer': 'get_customer_message', 'policy': 'get_policy'}
    for item in evidence:
        if tool_names[item.source] not in accessed:
            raise ReviewExecutionError('evidence_not_read', '인용한 자료를 실제 조회하지 않아 보고서를 검증하지 못했습니다.')
        if not item.quote.strip() or item.quote not in texts[item.source]:
            raise ReviewExecutionError('invalid_evidence', '보고서 인용이 조회한 고객 원문 또는 정책의 실제 구절과 일치하지 않습니다.')


def validate_report(report: BaseModel, request: ReviewRequest, draft: str, accessed: set[str]) -> None:
    """Check exact citations and verdict consistency without inventing findings."""
    if isinstance(report, (AnalysisReport, ResponseReport)):
        _validate_evidence(report.evidence, request, accessed)
        if isinstance(report, AnalysisReport) and report.requests and not any(item.source == 'customer' for item in report.evidence):
            raise ReviewExecutionError('missing_evidence', '분석한 고객 요구의 원문 인용이 없습니다.')
        return
    if not isinstance(report, ReviewReport):
        raise ReviewExecutionError('invalid_report', '지원하지 않는 보고서 구조입니다.')
    for finding in report.findings:
        quote = finding.draft_quote
        if not quote.strip() and finding.category not in {'missing_request', 'missing_information'}:
            raise ReviewExecutionError('missing_draft_quote', '검수 지적에 해당하는 실제 초안 구절이 없습니다.')
        if quote and (not quote.strip() or quote not in draft):
            raise ReviewExecutionError('invalid_draft_quote', '검수 지적의 인용이 실제 응대 초안과 일치하지 않습니다.')
        _validate_evidence(finding.evidence, request, accessed)
        if finding.category == 'policy_conflict' and not any(item.source == 'policy' for item in finding.evidence):
            raise ReviewExecutionError('missing_policy_evidence', '정책 충돌 지적에 실제 정책 원문 인용이 없습니다.')
        if finding.category == 'missing_request' and not any(item.source == 'customer' for item in finding.evidence):
            raise ReviewExecutionError('missing_customer_evidence', '누락된 고객 요구를 확인할 원문 인용이 없습니다.')
    if report.verdict == 'pass' and (report.findings or report.missing_info):
        raise ReviewExecutionError('inconsistent_verdict', '통과 판정과 보고서의 지적 또는 확인 필요 사항이 일치하지 않습니다.')
    if report.verdict == 'revise' and not report.findings:
        raise ReviewExecutionError('inconsistent_verdict', '수정 판정의 실제 지적 사항이 없습니다.')
    if report.verdict == 'needs_info' and not report.missing_info and not any(item.category == 'missing_information' for item in report.findings):
        raise ReviewExecutionError('inconsistent_verdict', '추가 확인 판정의 부족한 정보가 명시되지 않았습니다.')



REQUIRED = {'get_customer_message','get_policy','get_reply_draft'}

def run_review(request, llm, agent_factory=create_deep_agent):
    request=ReviewRequest.model_validate(request)
    accessed=set()
    @tool
    def get_customer_message()->dict:
        """이번 검수의 실제 고객 문의 원문을 조회합니다."""
        accessed.add('get_customer_message')
        return {'source':'customer','text':request.customer_message}
    @tool
    def get_policy()->dict:
        """A4B 자료 읽기·검색에서 전달한 정책 원문을 조회합니다. 비어 있으면 정책을 추정하지 않습니다."""
        accessed.add('get_policy')
        return {'source':'policy','text':request.policy_text,'available':bool(request.policy_text.strip())}
    @tool
    def get_reply_draft()->dict:
        """검수할 실제 초안 원문을 조회합니다. 처리 완료·승인·발송은 수행하지 않습니다."""
        accessed.add('get_reply_draft')
        return {'source':'draft','text':request.reply_draft}
    @wrap_model_call
    def review_tools_only(req,handler):
        pending = sorted(REQUIRED - accessed)
        choice = {'type':'function','function':{'name':pending[0]}} if pending else None
        return handler(req.override(tools=[t for t in req.tools if getattr(t,'name',None) in REQUIRED or getattr(t,'name',None)=='ReviewReport'], response_format=None if pending else ToolStrategy(ReviewReport,handle_errors=True), tool_choice=choice, model_settings=req.model_settings))
    agent=agent_factory(model=llm,tools=[get_customer_message,get_policy,get_reply_draft],subagents=[],middleware=[review_tools_only],response_format=ToolStrategy(ReviewReport,handle_errors=False),name='junghyun_reply_review',system_prompt=COMMON+ROLE_PROMPTS['review']+'\n추가 검수 기준:\n'+request.review_rules)
    payload={'messages':[{'role':'user','content':'조회 도구에서 고객 원문·정책·초안을 먼저 읽고 ReviewReport를 반환하세요.'}]}
    for attempt in range(2):
        result=agent.invoke(payload,{'recursion_limit':20})
        messages=result.get('messages',[])
        report_indexes=[i for i,m in enumerate(messages) if isinstance(m,AIMessage) and any(t.get('name')=='ReviewReport' for t in m.tool_calls)]
        if not report_indexes:raise ValueError('검수 보고서 생성 이력을 확인하지 못했습니다.')
        reads={m.name for m in messages[:report_indexes[-1]] if isinstance(m,ToolMessage) and m.status=='success'}
        if not REQUIRED<=reads & accessed:raise ValueError('필수 원문·정책·초안을 실제 조회한 뒤 보고서를 생성해야 합니다.')
        if any(isinstance(m,ToolMessage) and m.name in REQUIRED and m.status=='error' for m in messages):raise ValueError('검수 자료 조회에 실패했습니다.')
        report=ReviewReport.model_validate(result.get('structured_response'))
        try:
            validate_report(report,request,request.reply_draft,accessed)
            break
        except ReviewExecutionError as error:
            if attempt == 1:
                raise
            payload={'messages':[*messages,{'role':'user','content':'보고서 검증 실패: '+error.public_message+' 조회한 원문에서 연속된 구절을 정확히 복사하여 인용하고 보고서를 수정하세요. 원문 내용을 바꾸지 마세요.'}]}
    usage={k:sum((getattr(m,'usage_metadata',None) or {}).get(k,0) for m in messages) for k in ['input_tokens','output_tokens','total_tokens']}
    return {'report':report.model_dump(),'tool_calls':sorted(reads),'usage':usage}

def review_node(node,state,storage,config,llm):
    inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    docs=inputs.get('documents',[])
    if 'documents' not in inputs:
        from .workflow_runtime import document_chunks
        ids=[id for id in state.get('file_ids',[]) if storage.asset(id)['kind']=='document'] or [id for id in config['document_ids'] if storage.asset(id)['kind']=='document']
        docs=document_chunks(storage,ids)
    if sum(len(d['text']) for d in docs)>12000:raise ValueError('검수 정책 범위는 12,000자까지입니다. 검색 노드로 관련 구간을 선택하세요.')
    policy='\n'.join(d['text'] for d in docs)
    if len(policy)>12000:raise ValueError('검수 정책 범위는 12,000자까지입니다.')
    rows=None
    if 'table' in inputs:
        fields=node['settings']['fields']
        if len(fields)!=2:raise ValueError('검수 fields에는 고객 문의 열·응대 초안 열을 순서대로 지정하세요.')
        table=inputs['table'];rows=table['rows']
        if len(rows)>10:raise ValueError('한 실행의 검수는 최대 10행입니다. 기존 필터로 대상을 줄이거나 파일을 나누세요.')
        if any(f not in table['columns'] for f in fields):raise ValueError('검수할 문의·초안 열을 찾을 수 없습니다.')
        pairs=[(row.get(fields[0]),row.get(fields[1])) for row in rows]
    else:
        pairs=[(state.get('customer_message') or state.get('messages',[{'content':state['request']}])[0]['content'],inputs.get('text') or state.get('reply_draft'))]
    # Validate every row before billing a model for any row.
    requests=[ReviewRequest(customer_message=customer,reply_draft=draft,policy_text=policy,review_rules=node['settings']['instructions'] or '') for customer,draft in pairs]
    sources=[]
    for d in docs:
        asset=storage.asset(d['file_id'])
        sources.append({k:d.get(k) for k in ['source_id','file_id','file_name','page','offset','text']} | {'version':asset.get('version',1),'hash':asset.get('hash')})
    reviews=[]
    for index,request in enumerate(requests):
        result=run_review(request,llm)
        for finding in result['report']['findings']:
            for evidence in finding['evidence']:
                if evidence['source']=='policy' and not any(evidence['quote'] in d['text'] for d in docs):raise ValueError('정책 인용이 실제 한 문서 구간의 원문과 일치하지 않습니다.')
        reviews.append({'id':node['id']+':'+str(index),'customer_message':request.customer_message,'reply_draft':request.reply_draft,'sources':sources,'source_row':rows[index] if rows is not None else None,**result})
    return {'reviews':reviews}
