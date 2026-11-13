# Executor module for each tool id. module_path is resolved at run time by
# workflow_engine.executors.dispatch; keep this table the single source of executor locations.
EXECUTORS = {
 'read_table':'workflow_engine.executors.table.read_table',
 'filter_sort':'workflow_engine.executors.table.filter_sort',
 'aggregate_table':'workflow_engine.executors.table.aggregate_table',
 'calculate_columns':'workflow_engine.executors.table.calculate_columns',
 'text_transform':'workflow_engine.executors.text.text_transform',
 'base64_codec':'workflow_engine.executors.text.base64_codec',
 'text_hash':'workflow_engine.executors.text.text_hash',
 'date_transform':'workflow_engine.executors.text.date_transform',
 'text_statistics':'workflow_engine.executors.text.text_statistics',
 'read_document':'workflow_engine.executors.document.read_document',
 'search_document':'workflow_engine.executors.document.search_document',
 'extract_information':'workflow_engine.executors.document.extract_information',
 'compose_answer':'workflow_engine.executors.document.compose_answer',
 'agent_node':'workflow_engine.executors.agent.agent_node',
 'condition':'workflow_engine.executors.control.condition',
 'ask_user':'workflow_engine.executors.control.ask_user',
 'finish':'workflow_engine.executors.control.finish',
 'saved_workflow':'workflow_engine.executors.control.saved_workflow',
 'write_result':'workflow_engine.executors.output.write_result',
 'export_result':'workflow_engine.executors.output.export_result',
 'review_summary':'workflow_engine.executors.output.review_summary',
 'reply_review':'workflow_engine.executors.review.reply_review',
}

TOOLS = [
 {'id':'read_table','name':'표 파일 읽기','description':'연결한 CSV·Excel·JSON 파일의 실제 행을 읽습니다.','inputs':{'file_id':'연결 자료 ID'}},
 {'id':'filter_sort','name':'조건에 따라 정리','description':'여러 조건의 값·열 비교, 숫자·문자 정렬, 중복 및 결측 정책을 적용합니다.','inputs':{'filters':'조건 목록','sort':'정렬 목록','missing_policy':'ask / exclude / keep / error','duplicate_policy':'ask / keep / first / last / error'}},
 {'id':'write_result','name':'결과 파일 만들기','description':'실제 처리 결과를 CSV 또는 JSON으로 만듭니다. 외부 전송은 하지 않습니다.','inputs':{'format':'csv / json'}},
]

for item in TOOLS:
    item['version']=1
    item['module_path']=EXECUTORS[item['id']]+':execute'

# Ports are executable contracts, not model-written source code.
PORTS = {
 'agent_node':({'text':'text','documents':'documents','table':'table','reviews':'reviews'}, {'text':'text'}),
 'aggregate_table':({'table':'table'}, {'table':'table'}),
 'calculate_columns':({'table':'table'}, {'table':'table'}),
 'text_transform':({'text':'text'}, {'text':'text'}),
 'base64_codec':({'text':'text'}, {'text':'text'}),
 'text_hash':({'text':'text'}, {'text':'text'}),
 'date_transform':({'text':'text'}, {'text':'text'}),
 'text_statistics':({'text':'text'}, {'records':'records'}),
 'finish':({'text':'text','artifact':'artifact'}, {'text':'text','artifact':'artifact'}),
 'read_table':({}, {'table':'table'}),
 'filter_sort':({'table':'table'}, {'table':'table'}),
 'write_result':({'table':'table'}, {'artifact':'artifact'}),
 'read_document':({}, {'documents':'documents'}),
 'search_document':({'documents':'documents'}, {'documents':'documents'}),
 'extract_information':({'documents':'documents'}, {'records':'records'}),
 'reply_review':({'documents':'documents','text':'text','table':'table'}, {'reviews':'reviews'}),
 'compose_answer':({'reviews':'reviews','documents':'documents','answer':'text'}, {'text':'text'}),
 'export_result':({'records':'records','text':'text','answer':'text'}, {'artifact':'artifact'}),
 'condition':({'table':'table','reviews':'reviews','records':'records','text':'text','answer':'text','documents':'documents'}, {'table':'table','reviews':'reviews','records':'records','text':'text','answer':'text','documents':'documents','decision':'boolean'}),
 'ask_user':({'records':'records','text':'text','documents':'documents'}, {'records':'records','text':'text','documents':'documents','answer':'text'}),
}
for id,name,description in [
 ('agent_node','에이전트 실행','저장한 역할의 node_ref 버전을 사용하거나 새 역할의 settings.instructions를 구성합니다. 실행 요청·연결 text/문서/표를 실제 조회해 text를 반환합니다. 검수 후 수정에는 reviews 1건을 명시적으로 연결하며 최신 수정문은 승인 후보로 보존됩니다.'),
 ('aggregate_table','목록 집계','fields로 그룹별 집계합니다. aggregation=count는 건수, sum은 sum_field의 숫자 합계입니다. 빈 fields는 전체 집계. 출력 열은 fields와 count 또는 sum입니다. 합계 후 정렬·필터는 sum 열을 사용합니다. 빈값·비숫자 합계는 실패합니다.'),
 ('calculate_columns','표 열 계산·이름 변경','입력 표에 새 계산 열을 만들거나 열 이름 하나를 바꾸고, 날짜 문자열 열을 지정 형식과 시간대로 변환합니다. compute는 calculation_formula와 output_column, rename은 rename_from과 rename_to, parse_dates는 fields와 date_input_format, date_timezone이 필요합니다. 수식은 식별자 형태의 실제 열 이름과 숫자, + - * / % 및 괄호만 사용합니다. 입력 오류·계산 열 덮어쓰기는 실패합니다.'),
 ('text_transform','문자열 바꾸기','앞 단계 텍스트에서 text_find를 text_replacement로 그대로 바꿉니다. 빈 문자열로 교체하여 삭제할 수 있습니다. 문서 파일을 직접 편집하거나 정규식을 실행하지 않습니다.'),
 ('base64_codec','Base64 문자열 변환','앞 단계 UTF-8 텍스트를 Base64로 인코딩하거나 디코딩합니다. codec_mode=encode 또는 decode가 필요합니다.'),
 ('text_hash','문자열 SHA-256','앞 단계 UTF-8 텍스트의 SHA-256 해시를 구합니다. 파일 전체의 해시는 구하지 않습니다.'),
 ('date_transform','날짜·시간 문자열 변환','앞 단계 ISO 날짜·시간 텍스트를 지정한 초만큼 조정하거나 지정한 형식으로 표시합니다. date_operation=adjust는 date_seconds와 시간대가 포함된 입력이 필요하고 format은 date_output_format이 필요합니다.'),
 ('text_statistics','문자·단어 수 세기','앞 단계 텍스트의 글자 수 또는 공백 기준 단어 수를 센 결과를 records로 반환합니다. statistics_unit은 characters 또는 words이며 글자 수는 include_whitespace를 명시합니다.'),
 ('finish','완료 안내','설정한 완료·중단 안내를 표시하고 흐름을 끝냅니다.'),
 ('read_document','문서 읽기','연결 문서 또는 이번 실행의 새 문서를 페이지·출처와 함께 읽습니다.'),
 ('search_document','관련 내용 검색','질문과 일치하는 문서 구간을 단어 기반 검색합니다. 의미 검색은 아직 지원하지 않습니다.'),
 ('extract_information','정보 추출','설정한 항목을 추출하고 실제 원문 인용을 검증합니다. 없는 값은 누락으로 표시합니다.'),
 ('reply_review','응대 초안 검수','고객 원문·정책·초안을 조회해 판정·문제 구절·수정안을 보고합니다. fields=[문의 열, 초안 열]은 표 검수이며 최대 10행입니다. reviews를 후속 분기·보고서 정리·수정 단계로 전달합니다. 승인·거절은 실행 결과에서 사람이 결정합니다. 단일 검수의 고객 문의·초안은 실행 화면 외부 입력이므로 bindings=[] 또는 documents만 연결합니다. 가짜 start/request binding은 금지합니다.'),
 ('compose_answer','근거 기반 답변','검색된 원문만 바탕으로 답변 또는 요약을 작성합니다.'),
 ('export_result','결과 저장','추출 표를 CSV/JSON, 답변을 Markdown 파일로 만듭니다.'),
 ('condition','조건 분기','누락 항목 또는 빈 결과 여부에 따라 yes/no 경로를 선택합니다.'),
 ('ask_user','사용자 확인','실행을 멈추고 확인 답변을 받아 동일한 실행을 이어갑니다.'),
]:
 TOOLS.append({'id':id,'name':name,'description':description,'version':1,'module_path':EXECUTORS[id]+':execute','inputs':PORTS[id][0],'outputs':PORTS[id][1]})
for item in TOOLS:
 item.setdefault('outputs',PORTS[item['id']][1])
 item['input_ports']=PORTS[item['id']][0]
 item['output_ports']=PORTS[item['id']][1]

for item in TOOLS:item['module_path']=EXECUTORS[item['id']]+':execute'
PORTS['review_summary']=({'reviews':'reviews'},{'table':'table'})
PORTS['saved_workflow']=({'table':'table','documents':'documents','records':'records','text':'text','reviews':'reviews','artifact':'artifact'},{'table':'table','documents':'documents','records':'records','text':'text','reviews':'reviews','artifact':'artifact'})
for id,name,description in [
 ('review_summary','검수 보고서 정리','검수 결과를 보고서 표로 정리합니다. 수정안은 승인 전 후보이며 최종 승인 응대가 아닙니다.'),
 ('saved_workflow','저장된 워크플로우 실행','저장한 에이전트의 특정 버전을 노드로 재사용합니다. agent_id/agent_version/output_node/output_port를 지정합니다. 앞 단계 table/documents/text를 전달하고 선택한 출력 포트를 다음 단계로 연결합니다. 동일 모델·계정만 지원하며 재귀 호출은 금지됩니다.'),
]:TOOLS.append({'id':id,'name':name,'description':description,'version':1,'module_path':EXECUTORS[id]+':execute','inputs':PORTS[id][0],'outputs':PORTS[id][1],'input_ports':PORTS[id][0],'output_ports':PORTS[id][1]})
# Ports can vary by instance, but never by the identity of a predecessor/successor.
def node_ports(node):
    inputs,outputs=PORTS[node['tool']]
    if node['tool']=='extract_information' and node['settings'].get('extraction_mode') in ('fields','receipt_items'):
        return inputs, {**outputs, 'table':'table'}
    if node['tool']=='saved_workflow':
        port=node['settings'].get('output_port');return inputs,{port:outputs[port]} if port in outputs else {}
    return inputs,outputs

# One catalogue for all 56 business tools; retain the six control/compatibility IDs.
NATIVE_IDS = frozenset(EXECUTORS)
from workflow_engine.catalog.canonical import BUSINESS_TOOLS, BUSINESS_IDS, OPERATIONS, contract, operation_spec, ports
for business in [*BUSINESS_TOOLS, {'id': 'slack.post_message', 'name': 'Slack 메시지 보내기',
                                   'description': '앞 단계의 text 출력을 지정한 Slack 채널에 전송합니다.'}]:
    identifier = business['id']
    contracts = [contract(identifier, spec) for spec in OPERATIONS[identifier]]
    item = next((t for t in TOOLS if t['id'] == identifier), None)
    if item is None:
        EXECUTORS[identifier] = 'workflow_engine.executors.canonical_operation'
        # Catalogue ports are the union for discovery; node_ports uses the selected operation.
        inputs, outputs = {}, {}
        for c in contracts:
            for name,kind in c['input_ports'].items():
                inputs[name]=kind if name not in inputs or inputs[name]==kind else 'json'
            for name,kind in c['output_ports'].items():
                outputs[name]=kind if name not in outputs or outputs[name]==kind else 'json'
        PORTS[identifier] = inputs, outputs
        item = {'id': identifier, 'name': business['name'], 'description': business['description'],
                'version': 1, 'module_path': EXECUTORS[identifier]+':execute',
                'inputs': inputs, 'outputs': outputs, 'input_ports': inputs, 'output_ports': outputs}
        TOOLS.append(item)
    item['business_tool'] = identifier in BUSINESS_IDS
    item['operation_contracts'] = contracts
    item['description'] += ' 확장 동작은 operation_contracts의 operation·인자·범위를 사용합니다.'
    item['execution_status'] = ('native_ready' if identifier in NATIVE_IDS else
                                'connection_required' if all(c['status'] == 'external_connection_required' for c in contracts)
                                else 'registered')

_native_node_ports = node_ports

def uses_operation(node):
    return bool(node['settings'].get('operation')) or node['tool'] not in NATIVE_IDS


def node_ports(node):
    if uses_operation(node):
        try:
            spec = operation_spec(node['tool'], node['settings'].get('operation'))
            inputs, outputs = ports(node['tool'], spec, node['settings'].get('output_port'))
            if node['tool'] == 'create_spreadsheet' and spec['id'] == 'create':
                from workflow_engine.catalog.canonical import parse_arguments
                sheets = parse_arguments(node['settings']).get('sheets', [])
                if isinstance(sheets, list):
                    for sheet in sheets:
                        table = sheet.get('table') if isinstance(sheet, dict) else None
                        if isinstance(table, dict) and set(table) == {'$binding'}:
                            alias = table['$binding']
                            if isinstance(alias, str) and alias.isidentifier():
                                inputs[alias] = 'table'
            return inputs, outputs
        except ValueError: return {}, {}
    return _native_node_ports(node)
