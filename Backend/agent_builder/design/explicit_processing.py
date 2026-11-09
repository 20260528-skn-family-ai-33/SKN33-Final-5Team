"""Check narrow, explicit processing requirements against the actual data path.

This is not a general semantic approval. Unknown wording is left to the normal
requirement/verification pipeline; these diagnostics never construct a graph.
"""
import re
from .content_lineage import content_ancestors
from .requirement_coverage import _formula
from workflow_engine.catalog.canonical import parse_arguments


def _requests(state):
    return [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']


def literal_products(state):
    # Only a written product of two actual columns is unambiguous here. Do not
    # infer formulas from prose or use document content as user instructions.
    columns = {c for r in state.get('resources', []) if r.get('kind') == 'table'
               for c in r.get('columns', []) if c.isidentifier()}
    products = set()
    for text in _requests(state):
        # A later cancellation/replacement invalidates this narrow extraction.
        # The general requirement ledger must resolve the replacement instead.
        if re.search(r'계산.{0,8}(?:하지\s*(?:마|말)|하지\s*않|빼|제외|취소)|(?:말고|대신)', text):
            products.clear()
            continue
        if not re.search(r'계산|곱|합산|합계', text): continue
        for left in columns:
            for right in columns:
                pattern = rf'(?<!\w){re.escape(left)}\s*[*×]\s*{re.escape(right)}(?=$|\s|[.,;:)]|(?:를|을|로|와|과|에)(?:\s|$))'
                if re.search(pattern, text): products.add(left+' * '+right)
    return sorted(products)


def explicit_processing_issues(state, draft):
    workflow = draft.get('workflow')
    if not workflow or draft.get('questions') or draft.get('unsupported'):
        return []
    nodes = {n['id']: n for n in workflow['nodes']}
    lineage = {key: content_ancestors(nodes, key) for key in nodes}
    issues = []
    for formula in literal_products(state):
        matching = set()
        for key, node in nodes.items():
            settings = node['settings']
            if node['tool'] != 'calculate_columns': continue
            try:
                if settings.get('operation') in ('compute', 'calculate_records'):
                    actual = parse_arguments(settings).get('formula')
                elif not settings.get('operation') and settings.get('table_operation') == 'compute':
                    actual = settings.get('calculation_formula')
                else: continue
                if _formula(actual) == _formula(formula): matching.add(key)
            except (ValueError, TypeError, SyntaxError): continue
        sinks = [key for key, n in nodes.items() if n['tool'] in ('write_result', 'export_result')]
        if not matching or sinks and not any(m in lineage[s] for m in matching for s in sinks):
            issues.append(f'사용자가 명시한 {formula} 계산이 실제 결과 경로에 없습니다. calculate_columns의 compute 단계와 계산식·출력 열을 구성하고, 합계가 그 계산 결과를 입력받도록 연결하세요. 설명문만으로 계산을 대신할 수 없습니다.')

    review = False; suggestion = False; human = False
    for text in _requests(state):
        if re.search(r'(?:검토|분석|수정\s*제안).{0,8}(?:하지\s*(?:마|말)|하지\s*않|빼|제외|취소)|(?:검토|분석)\s*말고', text):
            review = suggestion = human = False
            continue
        if re.search(r'모순|상충', text) and re.search(r'검토|분석|찾|확인', text): review = True
        if re.search(r'수정\s*(?:제안|안)|개선\s*안', text): suggestion = True
        if re.search(r'(?:사람|담당자).{0,20}확인', text): human = True
    if review:
        analysis = []
        for key, node in nodes.items():
            if node['tool'] not in ('compose_answer', 'agent_node', 'review_document'): continue
            if not node.get('bindings'): continue
            instructions = str(node['settings'].get('instructions') or '') + str(node['settings'].get('arguments_json') or '')
            if node['tool'] in ('compose_answer','agent_node'):
                instructions += '\n' + draft.get('instructions','')
            if not re.search(r'모순|상충', instructions): continue
            if suggestion and not re.search(r'수정\s*(?:제안|안)|개선\s*안', instructions): continue
            analysis.append(key)
        if not analysis:
            issues.append('요청한 문서 모순 분석'+('·수정 제안' if suggestion else '')+' 단계가 없습니다. 항목 추출은 분석이 아닙니다. 원문 documents를 compose_answer 등 실제 분석 도구에 연결하고 요청한 검토 기준을 실행 지시사항에 포함하세요.')
        elif human and not any(n['tool']=='ask_user' and any(
                b['input']=='text' and b['output']=='text' and any(
                    a == b['node_id'] or a in lineage.get(b['node_id'],set()) for a in analysis)
                for b in n.get('bindings',[])) for n in nodes.values()):
            issues.append('사람 확인 단계에 문서 분석 결과가 연결되지 않았습니다. 원문이나 추출 항목만 보여주지 말고 분석 결과 text를 ask_user에 전달하세요.')
    return issues
