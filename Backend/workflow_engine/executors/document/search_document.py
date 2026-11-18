import re


from documents.resources.document_resources import TABLE_SCOPE_INCOMPLETE, input_document_scope
from workflow_engine.execution_path import document_table_scope_incomplete
from workflow_engine.executors.schemas import SearchTerms


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    import math, unicodedata
    query = settings['query'] or state['request']
    rewrite = llm.with_structured_output(SearchTerms, method='json_schema', strict=True).invoke([{'role': 'system', 'content': '질문에서 문서 검색에 필요한 핵심 명사·오류 문구를 최대 8개 추출하세요. 질문에 없는 모델명·사실은 추가하지 마세요. 조사와 요청 표현은 빼세요.'}, {'role': 'user', 'content': query}])
    normalize = lambda v: re.sub('\\s+', '', unicodedata.normalize('NFKC', v).lower())
    terms = [normalize(t) for t in rewrite.keywords[:8] if t.strip()]
    docs = inputs['documents']
    corpus = [normalize(d['text']) for d in docs]
    weights = {t: math.log(1 + len(docs) / (1 + sum((t in text for text in corpus)))) for t in terms}
    scored = [(sum((weights[t] for t in terms if t in text)), i, d) for i, (text, d) in enumerate(zip(corpus, docs))]
    matches = [(i, d) for score, i, d in sorted(scored, key=lambda v: (-v[0], v[1]))[:8] if score > 0]
    selected_indices = {i for i, _ in matches}
    scope = input_document_scope(node, state['outputs'])
    # The selected table chunk contains all native cells, including an empty list
    # when the parser detected a table without confirming its coordinates.
    incomplete = scope.get(TABLE_SCOPE_INCOMPLETE, False) or document_table_scope_incomplete(node, state['outputs'], config) or any(
        part.get(TABLE_SCOPE_INCOMPLETE) or ('table_cells' in part and i not in selected_indices)
        for i, part in enumerate(docs)
    )
    selected = [{**part, TABLE_SCOPE_INCOMPLETE: True} if incomplete else part for _, part in matches]
    # Keep the verified scope even when a search forwarded no chunks.
    return {**scope, TABLE_SCOPE_INCOMPLETE: incomplete, 'documents': selected, 'query': query,
            'keywords': rewrite.keywords[:8], 'matched_count': len(selected)}
