/** Presentation copy only. Tool IDs, contracts and saved user descriptions stay unchanged. */
const descriptions: Record<string, string> = {
    read_table: 'CSV·Excel·JSON 표 파일의 열과 데이터를 읽습니다.',
    filter_sort: '조건에 맞는 행을 고르고, 지정한 순서로 정렬합니다.',
    write_result: '처리한 표를 CSV 또는 JSON 파일로 저장합니다.',
    agent_node: '지시사항과 자료를 바탕으로 내용을 분석하거나 답변을 작성합니다.',
    aggregate_table: '전체 또는 그룹별로 건수를 세거나 숫자의 합계를 구합니다.',
    finish: '완료 또는 중단 안내를 표시하고 처리를 마칩니다.',
    read_document: '첨부한 문서에서 텍스트와 페이지별 내용을 읽습니다.',
    search_document: '읽은 문서에서 검색어와 관련된 구간을 찾습니다.',
    extract_information: '문서에서 요청한 항목을 찾아 원문 근거와 함께 정리합니다.',
    reply_review: '응대 초안을 문서와 검수 기준에 대조해 확인할 부분을 찾습니다.',
    compose_answer: '앞에서 확인한 내용과 근거를 모아 답변을 작성합니다.',
    export_result: '추출한 내용이나 답변을 결과 파일로 저장합니다.',
    condition: '조건의 충족 여부에 따라 다음 처리 경로를 나눕니다.',
    ask_user: '처리에 필요한 내용을 사용자에게 질문하고 답변을 기다립니다.',
    review_summary: '검수 결과와 수정안을 보고서 표로 정리합니다. 수정안은 승인 전 초안입니다.',
    saved_workflow: '지정한 버전의 저장된 워크플로우를 실행합니다.',
};
export function toolDescription(id: string, fallback: string) { return descriptions[id] || fallback; }
export function presetDescription(spec: {
    kind: string;
    tool: string;
    purpose: string;
    settings: Record<string, unknown>;
}) {
    if (spec.kind === 'agent')
        return spec.purpose;
    if (spec.tool === 'aggregate_table' && spec.settings.aggregation === 'count' && (!Array.isArray(spec.settings.fields) || !spec.settings.fields.length))
        return '표에 있는 전체 행 수를 셉니다.';
    return toolDescription(spec.tool, spec.purpose);
}
