TOOLS = [
 {'id':'read_table','name':'표 파일 읽기','description':'연결한 CSV·Excel·JSON 파일의 실제 행을 읽습니다.','inputs':{'file_id':'연결 자료 ID'}},
 {'id':'filter_sort','name':'조건에 따라 정리','description':'여러 조건의 값·열 비교, 숫자·문자 정렬, 중복 및 결측 정책을 적용합니다.','inputs':{'filters':'조건 목록','sort':'정렬 목록','missing_policy':'ask / exclude / keep / error','duplicate_policy':'ask / keep / first / last / error'}},
 {'id':'write_result','name':'결과 파일 만들기','description':'실제 처리 결과를 CSV 또는 JSON으로 만듭니다. 외부 전송은 하지 않습니다.','inputs':{'format':'csv / json'}},
]

for item in TOOLS:
    item['version']=1
    item['module_path']='agent_builder.tools.'+item['id']+':'+item['id']+'_node'

# Ports are executable contracts, not model-written source code.
PORTS = {
 'agent_node':({'text':'text','documents':'documents','table':'table','reviews':'reviews'}, {'text':'text'}),
 'aggregate_table':({'table':'table'}, {'table':'table'}),
 'calculate_columns':({'table':'table'}, {'table':'table'}),
 'scalar_calculate':({}, {'text':'text'}),
 'text_transform':({'text':'text'}, {'text':'text'}),
 'base64_codec':({'text':'text'}, {'text':'text'}),
 'text_hash':({'text':'text'}, {'text':'text'}),
 'date_transform':({'text':'text'}, {'text':'text'}),
 'text_statistics':({'text':'text'}, {'records':'records'}),
 'json_transform':({'text':'text'}, {'text':'text'}),
 'join_tables':({'left':'table','right':'table'}, {'table':'table'}),
 'list_transform':({'text':'text'}, {'text':'text'}),
 'compare_tables':({'baseline':'table','candidate':'table'}, {'text':'text'}),
 'finish':({'text':'text','artifact':'artifact'}, {'text':'text','artifact':'artifact'}),
 'read_table':({'file_id':'file'}, {'table':'table'}),
 'filter_sort':({'table':'table'}, {'table':'table'}),
 'write_result':({'table':'table'}, {'artifact':'artifact'}),
 'read_document':({}, {'documents':'documents'}),
 'search_document':({'documents':'documents'}, {'documents':'documents'}),
 'extract_information':({'documents':'documents'}, {'records':'records'}),
 'reply_review':({'documents':'documents','text':'text','table':'table'}, {'reviews':'reviews'}),
 'compose_answer':({'reviews':'reviews','documents':'documents','answer':'text'}, {'text':'text'}),
 'export_result':({'records':'records','text':'text','documents':'documents','answer':'text'}, {'artifact':'artifact'}),
 'condition':({'table':'table','reviews':'reviews','records':'records','text':'text','answer':'text','documents':'documents'}, {'table':'table','reviews':'reviews','records':'records','text':'text','answer':'text','documents':'documents','decision':'boolean'}),
 'ask_user':({'records':'records','text':'text','documents':'documents'}, {'records':'records','text':'text','documents':'documents','answer':'text'}),
}
for id,name,description in [
 ('agent_node','에이전트 실행','저장한 역할의 node_ref 버전을 사용하거나 새 역할의 settings.instructions를 구성합니다. 실행 요청·연결 text/문서/표를 실제 조회해 text를 반환합니다. 검수 후 수정에는 reviews 1건을 명시적으로 연결하며 최신 수정문은 승인 후보로 보존됩니다.'),
 ('aggregate_table','목록 집계','fields로 그룹별 집계합니다. aggregation=count는 건수, sum은 sum_field의 숫자 합계입니다. 빈 fields는 전체 집계. 출력 열은 fields와 count 또는 sum입니다. 합계 후 정렬·필터는 sum 열을 사용합니다. 빈값·비숫자 합계는 실패합니다.'),
 ('calculate_columns','표 열 계산·이름 변경','입력 표에 새 계산 열을 만들거나 열 이름 하나를 바꾸고, 날짜 문자열 열을 지정 형식과 시간대로 변환합니다. compute는 calculation_formula와 output_column, rename은 rename_from과 rename_to, parse_dates는 fields와 date_input_format, date_timezone이 필요합니다. 수식은 식별자 형태의 실제 열 이름과 숫자, + - * / % 및 괄호만 사용합니다. 입력 오류·계산 열 덮어쓰기는 실패합니다.'),
 ('scalar_calculate','단일 수식 계산','숫자 상수와 사칙연산·나머지·괄호만 포함한 calculation_formula를 계산하고 JSON 숫자 텍스트를 반환합니다. 변수는 지원하지 않습니다.'),
 ('text_transform','문자열 바꾸기','앞 단계 텍스트에서 text_find를 text_replacement로 그대로 바꿉니다. 빈 문자열로 교체하여 삭제할 수 있습니다. 문서 파일을 직접 편집하거나 정규식을 실행하지 않습니다.'),
 ('base64_codec','Base64 문자열 변환','앞 단계 UTF-8 텍스트를 Base64로 인코딩하거나 디코딩합니다. codec_mode=encode 또는 decode가 필요합니다.'),
 ('text_hash','문자열 SHA-256','앞 단계 UTF-8 텍스트의 SHA-256 해시를 구합니다. 파일 전체의 해시는 구하지 않습니다.'),
 ('date_transform','날짜·시간 문자열 변환','앞 단계 ISO 날짜·시간 텍스트를 지정한 초만큼 조정하거나 지정한 형식으로 표시합니다. date_operation=adjust는 date_seconds와 시간대가 포함된 입력이 필요하고 format은 date_output_format이 필요합니다.'),
 ('text_statistics','문자·단어 수 세기','앞 단계 텍스트의 글자 수 또는 공백 기준 단어 수를 센 결과를 records로 반환합니다. statistics_unit은 characters 또는 words이며 글자 수는 include_whitespace를 명시합니다.'),
 ('json_transform','JSON 해석·값 조회','앞 단계 text를 JSON으로 해석합니다. json_operation=parse는 전체 값을, lookup은 json_path의 문자열 키·0 이상 배열 인덱스 경로의 값을 JSON 텍스트로 반환합니다. 파일을 직접 읽거나 수정하지 않습니다.'),
 ('join_tables','두 표 결합','앞 단계의 왼쪽(left)과 오른쪽(right) 표를 같은 이름의 join_keys 열로 결합합니다. join_how는 inner 또는 left, join_cardinality는 one_to_one 또는 many_to_one을 명시합니다. 오른쪽 키 중복과 빈 키는 오류입니다. 겹치는 오른쪽 열은 _right가 붙습니다.'),
 ('list_transform','목록 항목 선택·개수','앞 단계의 JSON 배열 텍스트에서 list_operation=count로 항목 수를 세거나 select와 0부터 시작하는 list_index로 항목을 가져옵니다. 결과는 JSON 텍스트입니다.'),
 ('compare_tables','두 표 차이 확인','기준(baseline)과 비교(candidate) 표에서 compare_keys를 기준으로 추가·삭제·변경을 찾습니다. compare_columns와 0 이상의 compare_tolerance를 명시합니다. 키는 중복·빈 값이 없어야 하며 숫자형끼리만 허용 오차를 적용합니다. JSON text를 반환합니다.'),
 ('finish','완료 안내','설정한 완료·중단 안내를 표시하고 흐름을 끝냅니다.'),
 ('read_document','문서 읽기','연결 문서 또는 이번 실행의 새 문서를 페이지·출처와 함께 읽습니다.'),
 ('search_document','관련 내용 검색','질문과 일치하는 문서 구간을 단어 기반 검색합니다. 의미 검색은 아직 지원하지 않습니다.'),
 ('extract_information','정보 추출','설정한 항목을 추출하고 실제 원문 인용을 검증합니다. 없는 값은 누락으로 표시합니다.'),
 ('reply_review','응대 초안 검수','고객 원문·정책·초안을 조회해 판정·문제 구절·수정안을 보고합니다. fields=[문의 열, 초안 열]은 표 검수이며 최대 10행입니다. reviews를 후속 분기·보고서 정리·수정 단계로 전달합니다. 승인·거절은 실행 결과에서 사람이 결정합니다. 단일 검수의 고객 문의·초안은 실행 화면 외부 입력이므로 bindings=[] 또는 documents만 연결합니다. 가짜 start/request binding은 금지합니다.'),
 ('compose_answer','근거 기반 답변','검색된 원문만 바탕으로 답변 또는 요약을 작성합니다.'),
 ('export_result','결과 저장','추출 표를 CSV/JSON, 답변이나 문서를 Markdown/JSON 파일로 만듭니다. 문서는 다른 결과 입력과 섞지 않습니다.'),
 ('condition','조건 분기','누락 항목 또는 빈 결과 여부에 따라 yes/no 경로를 선택합니다.'),
 ('ask_user','사용자 확인','실행을 멈추고 확인 답변을 받아 동일한 실행을 이어갑니다.'),
]:
 TOOLS.append({'id':id,'name':name,'description':description,'version':1,'module_path':'agent_builder.workflow_runtime:execute_node','inputs':PORTS[id][0],'outputs':PORTS[id][1]})
for item in TOOLS:
 item.setdefault('outputs',PORTS[item['id']][1])
 item['input_ports']=PORTS[item['id']][0]
 item['output_ports']=PORTS[item['id']][1]

for item in TOOLS:item['module_path']='agent_builder.executors.'+item['id']+':execute'
PORTS['review_summary']=({'reviews':'reviews'},{'table':'table'})
PORTS['saved_workflow']=({'table':'table','documents':'documents','records':'records','text':'text','reviews':'reviews','artifact':'artifact'},{'table':'table','documents':'documents','records':'records','text':'text','reviews':'reviews','artifact':'artifact'})
for id,name,description in [
 ('review_summary','검수 보고서 정리','검수 결과를 보고서 표로 정리합니다. 수정안은 승인 전 후보이며 최종 승인 응대가 아닙니다.'),
 ('saved_workflow','저장된 워크플로우 실행','저장한 에이전트의 특정 버전을 노드로 재사용합니다. agent_id/agent_version/output_node/output_port를 지정합니다. 앞 단계 table/documents/text를 전달하고 선택한 출력 포트를 다음 단계로 연결합니다. 동일 모델·계정만 지원하며 재귀 호출은 금지됩니다.'),
]:TOOLS.append({'id':id,'name':name,'description':description,'version':1,'module_path':'agent_builder.executors.'+id+':execute','inputs':PORTS[id][0],'outputs':PORTS[id][1],'input_ports':PORTS[id][0],'output_ports':PORTS[id][1]})
# Ports can vary by instance, but never by the identity of a predecessor/successor.
def node_ports(node):
    inputs,outputs=PORTS[node['tool']]
    if node['tool']=='saved_workflow':
        port=node['settings'].get('output_port');return inputs,{port:outputs[port]} if port in outputs else {}
    return inputs,outputs
