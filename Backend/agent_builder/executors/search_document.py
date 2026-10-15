import json,re
from pathlib import Path
from pydantic import BaseModel,ConfigDict
from langgraph.types import interrupt
from deepagents import create_deep_agent
from langchain_core.tools import tool as registered_tool
from langchain.agents.structured_output import ProviderStrategy
from langchain.agents.middleware import wrap_model_call
from ..workflow_runtime import Fact,Facts,SearchTerms,GroundedAnswer,Citation,Decision,InputAssessment,document_chunks,original_quote
from ..tools.filter_sort import filter_sort_node
from ..tools.write_result import write_result_node

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
    selected = [d for score, i, d in sorted(scored, key=lambda v: (-v[0], v[1]))[:8] if score > 0]
    return {'documents': selected, 'query': query, 'keywords': rewrite.keywords[:8], 'matched_count': len(selected)}
