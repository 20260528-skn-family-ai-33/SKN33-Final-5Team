"""도구 라벨 정의. A4B registry.py(4f71484)의 도구 16개 중 ML이 예측하는 14개."""

TOOLS = [
    'read_table', 'filter_sort', 'write_result', 'aggregate_table',
    'read_document', 'search_document', 'extract_information', 'compose_answer',
    'export_result', 'reply_review', 'review_summary', 'agent_node',
    'condition', 'ask_user',
]
KO = {
    'read_table': '표 읽기', 'filter_sort': '조건 정리', 'write_result': '결과 파일',
    'aggregate_table': '목록 집계', 'read_document': '문서 읽기', 'search_document': '관련 내용 검색',
    'extract_information': '정보 추출', 'compose_answer': '근거 기반 답변', 'export_result': '결과 저장',
    'reply_review': '응대 초안 검수', 'review_summary': '검수 보고서 정리', 'agent_node': '에이전트 실행',
    'condition': '조건 분기', 'ask_user': '사용자 확인',
    'finish': '완료 안내', 'saved_workflow': '저장된 워크플로우 실행',
}
IDX = {t: i for i, t in enumerate(TOOLS)}

# ML 대상이 아닌 도구: 완료 안내는 항상 넣고, 저장된 워크플로우는 저장본이 있을 때 규칙으로 넣는다.
ALWAYS = ['finish']
RULE_ONLY = ['saved_workflow']

# registry.py PORTS에서 가져온 입력 종류. 앞 단계에 이 종류를 내는 도구가 있어야 한다.
REQUIRES = {
    'filter_sort': 'table', 'aggregate_table': 'table', 'write_result': 'table',
    'search_document': 'documents', 'extract_information': 'documents', 'compose_answer': 'documents',
    'review_summary': 'reviews',
}
# 그 입력 종류가 처음 생기는 도구와, 그 도구를 넣으려면 필요한 자료 종류.
# 조건 정리나 집계도 표를 내보내지만 표를 받아야 돌아가므로, 흐름 맨 앞에는 결국 표 읽기가 있어야 한다.
BASE_PRODUCER = {'table': ('read_table', 'table'), 'documents': ('read_document', 'document'), 'reviews': ('reply_review', None)}


def to_vec(tool_ids):
    v = [0] * len(TOOLS)
    for t in tool_ids:
        if t in IDX:
            v[IDX[t]] = 1
    return v


def names(idx_list):
    return [KO[TOOLS[i]] for i in idx_list]
