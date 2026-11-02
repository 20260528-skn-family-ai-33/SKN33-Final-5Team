import json


from ..tools.write_result import write_result_node

def execute(node,state,storage,config,llm):
    from ..review_state import collect_reviews
    from ..reusable_workflows import REVIEW_EXPORT_ERROR,contains_review
    # Old pinned versions also run through this guard; they cannot bypass approval.
    if collect_reviews(state.get('outputs',{})) or contains_review(config):
        raise ValueError(REVIEW_EXPORT_ERROR)
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    if 'documents' in inputs:
        if len(inputs)!=1:raise ValueError('문서 저장에는 다른 결과 입력을 함께 연결할 수 없습니다.')
        documents=inputs['documents']
        if not isinstance(documents,list) or not documents or any(not isinstance(d,dict) or not isinstance(d.get('text'),str) for d in documents):
            raise ValueError('저장할 문서 내용이 없습니다.')
        if settings['format']=='json':
            return {'artifact':{'name':'문서_결과.json','content':json.dumps(documents,ensure_ascii=False,indent=2,allow_nan=False),'mime':'application/json; charset=utf-8'}}
        if settings['format']=='markdown':
            parts=[]
            for document in documents:
                source=str(document.get('file_name') or document.get('source_id') or '문서').replace('\n',' ').replace('\r',' ')
                page=document.get('page')
                heading='### '+source+(' · '+str(page)+'쪽' if page is not None else '')
                parts.append(heading+'\n\n'+document['text'])
            return {'artifact':{'name':'문서_결과.md','content':'\n\n'.join(parts),'mime':'text/markdown; charset=utf-8'}}
        raise ValueError('문서 결과는 Markdown 또는 JSON을 선택하세요.')
    if 'records' in inputs:
        rows = [{**r, 'evidence': json.dumps(r.get('evidence'), ensure_ascii=False)} for r in inputs['records']]
        ctx = {'rows': rows, 'columns': ['file_name', 'field', 'value', 'status', 'evidence'], 'trace': []}
        write_result_node(ctx, settings['format'])
        if inputs.get('answer') and settings['format'] == 'json':
            ctx['artifact']['content'] = json.dumps({'records': inputs['records'], 'user_confirmation': inputs['answer']}, ensure_ascii=False, indent=2)
        return {'artifact': ctx['artifact']}
    text=inputs.get('text') or inputs.get('answer')
    if not isinstance(text,str) or not text.strip():raise ValueError('저장할 텍스트 또는 추출 결과를 연결하세요.')
    if settings['format']=='json':
        try:parsed=json.loads(text)
        except (ValueError,TypeError):raise ValueError('JSON 결과가 올바른 JSON 문서가 아닙니다.') from None
        return {'artifact':{'name':'에이전트_결과.json','content':json.dumps(parsed,ensure_ascii=False,indent=2),'mime':'application/json; charset=utf-8'}}
    return {'artifact': {'name': '에이전트_결과.md', 'content': text, 'mime': 'text/markdown; charset=utf-8'}}
