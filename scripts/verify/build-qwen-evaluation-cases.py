"""Author the fixed synthetic gold corpus BEFORE inference. Never infer gold from results."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))
from agent_builder.conditions.clarification_contract import catalog_fingerprint
from workflow_engine.catalog.registry import TOOLS

TABLE = {'id': 'synthetic-sales', 'name': 'synthetic-sales.json', 'kind': 'table',
         'columns': ['id', 'qty', 'unit_price', 'amount', 'region', 'customer_id', 'date'],
         'rows': [{'id': 1, 'qty': 1, 'unit_price': 10, 'amount': 10, 'region': '서울', 'customer_id': 'A', 'date': '2026-11-03'},
                  {'id': 2, 'qty': 3, 'unit_price': 10, 'amount': 30, 'region': '부산', 'customer_id': 'B', 'date': '2026-11-05'},
                  {'id': 3, 'qty': 2, 'unit_price': 10, 'amount': 20, 'region': '서울', 'customer_id': 'A', 'date': '2026-11-08'}]}
DOC = {'id': 'synthetic-policy', 'name': 'synthetic-policy.txt', 'kind': 'document',
       'text': '합성 정책: 환불은 구매 후 7일 이내 신청할 수 있습니다. 고객 상담은 평일 09시부터 18시까지입니다.',
       'pages': ['합성 정책: 환불은 구매 후 7일 이내 신청할 수 있습니다. 고객 상담은 평일 09시부터 18시까지입니다.']}


def step(id, tool, operation=None, alternatives=()):
    return {'id': id, 'alternatives': [{'tool': tool, 'operation': operation}] + list(alternatives)}


READ = step('read', 'read_table', None, [{'tool': 'read_table', 'operation': 'parse'}])
FILTER = step('filter', 'filter_sort', None, [{'tool': 'filter_sort', 'operation': 'filter'}])
SORT = step('sort', 'filter_sort', None, [{'tool': 'filter_sort', 'operation': 'sort'}])
OUT = step('output', 'write_result', None, [{'tool': 'export_result', 'operation': 'write_json'}, {'tool': 'export_result', 'operation': 'serialize'}])
MAIL = [step('read', 'email.list', 'read_messages'), step('summary', 'compose_answer', None, [{'tool': 'compose_answer', 'operation': 'summarize'}])]
DOCUMENT = [step('read', 'read_document'), step('summary', 'compose_answer')]
cases = []


def add(group, text, route, intent, slots, steps=(), missing=None, answers=None, revision=None,
        resources='table', connected=None, reference='2026-11-10T01:00:00Z', zone='Asia/Seoul',
        rows=None, unsupported=None, final_slots=None, forbidden=None):
    idx = sum(c['category'] == group for c in cases) + 1
    prefix = {'ready': 'R', 'clarify': 'C', 'temporal': 'T', 'multiturn': 'M', 'unsupported': 'U', 'composite': 'X'}[group]
    missing = missing or {}
    resources = [TABLE] if resources == 'table' else [DOC] if resources == 'document' else []
    steps = list(steps)
    gold = {'intent': intent, 'slots': slots, 'missing_required_slots': list(missing),
            'routing': route, 'allowed_questions': missing,
            'forbidden_questions': forbidden or list(slots),
            'tools_and_operations': steps,
            'workflow': {'tasks': steps, 'dependencies': [[a['id'], b['id']] for a, b in zip(steps, steps[1:])],
                         'allow_shared_node_for_filter_sort': True, 'allow_optional_finish': True,
                         'forbid_unrequested_side_effects': True},
            'unsupported_actions': unsupported or [],
            'final_routing': 'READY' if answers or revision else route,
            'final_slots': final_slots or slots,
            'execution': {'mode': 'local_synthetic' if rows is not None else 'external_connector_not_executed' if any(s['alternatives'][0]['tool'].startswith(('email.', 'calendar.')) for s in steps) else 'not_scheduled',
                          'expected_rows': rows, 'external_execution_allowed': False}}
    cases.append({'id': f'{prefix}{idx:02}', 'category': group,
                  'split': 'holdout' if idx % 3 == 0 else 'development', 'eligible': True,
                  'request': text, 'prior_conversation': [], 'reference_time': reference, 'user_timezone': zone,
                  'resource_state': {'synthetic_only': True, 'resources': resources,
                                     'gmail_connected_metadata': connected, 'real_credentials_available': False},
                  'gold': gold, 'answer_policy': answers or [], 'revision_after_ready': revision,
                  'restart_between_turns': group == 'multiturn', 'max_user_answers': 4,
                  'adjudication': {'status': 'frozen_before_inference', 'basis': 'registered catalogue plus explicit request; no observed output',
                                   'natural_language_questions_require_review_if_match_is_ambiguous': True}})


POLICY = ' 빈 값은 오류, 중복은 유지해.'
# 10 clear requests, across mail, tables, document and scalar operations.
add('ready', 'Gmail에서 최근 5개 메일을 요약 정리해줘.', 'READY', 'summarize_email', {'gmail.limit': 5, 'provider': 'gmail'}, MAIL, resources=None, connected=False)
add('ready', '연결된 표에서 amount가 20 이상인 행만 JSON으로 저장해줘.'+POLICY, 'READY', 'filter_table', {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': 20, 'output.format': 'json'}, [READ,FILTER,OUT], rows=[TABLE['rows'][1],TABLE['rows'][2]])
add('ready', '연결한 표를 amount 숫자 내림차순으로 정렬하고 JSON으로 저장해줘.'+POLICY, 'READY', 'sort_table', {'sort.field':'amount','sort.direction':'descending','output.format':'json'}, [READ,SORT,OUT], rows=[TABLE['rows'][1],TABLE['rows'][2],TABLE['rows'][0]])
add('ready', '연결한 표에서 customer_id가 중복이면 첫 행만 남겨 JSON으로 저장해줘. 빈 값은 오류.', 'READY','deduplicate_table',{'duplicate.field':'customer_id','duplicate.policy':'first','output.format':'json'},[READ,step('deduplicate','filter_sort',None,[{'tool':'filter_sort','operation':'deduplicate'}]),OUT],rows=TABLE['rows'][:2])
add('ready', '연결한 표의 qty에 unit_price를 곱한 total 열을 만들고 JSON으로 저장해줘.'+POLICY,'READY','compute_table',{'compute.formula':'qty * unit_price','compute.output_column':'total','output.format':'json'},[READ,step('compute','calculate_columns',None,[{'tool':'calculate_columns','operation':'compute'}]),OUT],rows=[{**r,'total':r['qty']*r['unit_price']} for r in TABLE['rows']])
add('ready', '연결한 표를 region별 amount 합계로 집계하고 JSON으로 저장해줘. 빈 금액은 오류로 처리해.', 'READY','aggregate_table',{'aggregate.group_by':['region'],'aggregate.sum_field':'amount','output.format':'json'},[READ,step('aggregate','aggregate_table',None,[{'tool':'aggregate_table','operation':'sum'}]),OUT])
add('ready', '연결한 표의 amount 열 이름을 revenue로 바꾸고 JSON으로 저장해줘.', 'READY','rename_columns',{'rename.source':'amount','rename.target':'revenue','output.format':'json'},[READ,step('rename','calculate_columns',None,[{'tool':'calculate_columns','operation':'rename_columns'}]),OUT])
add('ready','연결한 정책 문서의 환불 규정을 요약해서 화면에 보여줘.','READY','summarize_document',{'source':'synthetic-policy','topic':'환불'},DOCUMENT,resources='document')
add('ready','문자열 hello를 UTF-8 Base64로 인코딩해서 보여줘.','READY','encode_text',{'text':'hello','mode':'encode'},[step('encode','base64_codec','convert')],resources=None)
add('ready','(12 + 8) * 3을 계산해서 결과를 보여줘.','READY','calculate_scalar',{'formula':'(12 + 8) * 3'},[step('calculate','scalar_calculate','evaluate')],resources=None)
# 10 genuinely missing decisions. Account connection itself is not a missing business slot.
add('clarify','매일 오전 9시에 매출 보고서를 이메일로 보내줘.','CLARIFY','scheduled_sales_email',{'schedule.frequency':'daily','schedule.hour':9,'schedule.minute':0},{},missing={'source':['자료','소스','데이터'],'recipient':['수신','주소','받을'],'report_scope':['보고서','분석','내용']},resources=None)
add('clarify','고객 데이터를 분석해서 보고서를 만들어줘.','CLARIFY','analyze_customers',{},missing={'source':['자료','데이터','소스'],'analysis_goal':['분석','기준','지표']},resources=None)
add('clarify','지난주 매출을 분석해서 이메일로 보내줘.','CLARIFY','sales_email',{'date_range.start':'2026-11-02','date_range.end_exclusive':'2026-11-09'},missing={'source':['자료','소스'],'recipient':['수신','주소'],'analysis_goal':['분석','지표']},resources=None)
add('clarify','연결한 표에서 amount가 큰 행만 JSON으로 저장해줘.'+POLICY,'CLARIFY','filter_table',{'filter.field':'amount','output.format':'json'},[READ,FILTER,OUT],missing={'threshold':['기준','얼마','금액','값'],'operator':['이상','초과','포함']})
add('clarify','연결한 표에서 중복을 제거하고 JSON으로 저장해줘. 빈 값은 오류로 처리해.','CLARIFY','deduplicate_table',{'output.format':'json'},missing={'duplicate_field':['열','항목','기준'],'duplicate_policy':['첫','마지막','보존','남길']})
add('clarify','Gmail에서 중요한 메일만 요약해줘.','CLARIFY','summarize_email',{'provider':'gmail'},MAIL,missing={'importance':['중요','기준']},resources=None,connected=True)
add('clarify','한국 시간으로 매주 표를 JSON으로 저장해줘.','CLARIFY','schedule_table',{'schedule.frequency':'weekly','schedule.timezone':'Asia/Seoul','output.format':'json'},[READ,OUT],missing={'weekday':['요일'],'hour':['시각','시간','몇 시']})
add('clarify','한국 시간으로 매일 표를 JSON으로 저장해줘.','CLARIFY','schedule_table',{'schedule.frequency':'daily','schedule.timezone':'Asia/Seoul','output.format':'json'},[READ,OUT],missing={'hour':['시각','몇 시','시간']})
add('clarify','연결한 표에 새 계산 열을 만들고 JSON으로 저장해줘.','CLARIFY','compute_table',{'output.format':'json'},missing={'formula':['계산','수식'],'column':['열 이름','새 열']})
add('clarify','Gmail로 제목 점검, 본문 완료라는 이메일을 보내줘.','CLARIFY','send_email',{'subject':'점검','body':'완료'},[step('send','email.send','execute')],missing={'recipient':['받는','수신','주소']},resources=None)
# Eight calendar/count cases, not eight paraphrases.
add('temporal','Gmail에서 어제 온 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-11-09'},MAIL,resources=None,connected=True)
add('temporal','Gmail에서 오늘 받은 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'today','gmail.date':'2026-11-09'},MAIL,resources=None,zone='America/New_York')
add('temporal','Gmail에서 어제 받은 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-11-08'},MAIL,resources=None,reference='2026-11-09T14:59:59Z')
add('temporal','Gmail에서 어제 받은 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-12-31'},MAIL,resources=None,reference='2027-01-01T01:00:00Z')
add('temporal','Gmail에서 어제 받은 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-03-08','gmail.window_hours':23},MAIL,resources=None,reference='2026-03-09T12:00:00Z',zone='America/New_York')
add('temporal','Gmail에서 어제 받은 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-11-01','gmail.window_hours':25},MAIL,resources=None,reference='2026-11-02T12:00:00Z',zone='America/New_York')
add('temporal','Gmail에서 최신 메일 다섯 통의 내용을 요약해줘.','READY','summarize_email',{'gmail.limit':5},MAIL,resources=None)
add('temporal','한국 시간으로 매일 오전 9시에 Gmail의 전날 메일을 요약해줘.','READY','scheduled_email_summary',{'schedule.frequency':'daily','schedule.hour':9,'schedule.minute':0,'schedule.timezone':'Asia/Seoul','gmail.query':'{{gmail.previous_day}}'},MAIL,resources=None)
# Ten actual clarification loops plus two READY revisions. Answers are selected ONLY from emitted questions.
def answer(slot, match, text): return {'slot':slot,'question_terms':match,'text':text}
add('multiturn','연결한 표에서 amount가 큰 행만 JSON으로 저장해줘.'+POLICY,'CLARIFY','filter_table',{'filter.field':'amount','output.format':'json'},[READ,FILTER,OUT],missing={'threshold':['기준','얼마','값'],'operator':['포함','초과','이상']},answers=[answer('threshold',['기준','얼마','값'],'20 이상으로 해줘.'),answer('operator',['포함','초과','이상'],'20 이상으로 해줘.')],final_slots={'filter.field':'amount','filter.value':20,'filter.operator':'gte','output.format':'json'},rows=[TABLE['rows'][1],TABLE['rows'][2]])
add('multiturn','연결한 표에서 customer_id가 중복인 행을 정리해서 JSON으로 저장해줘. 빈 값은 오류.','CLARIFY','deduplicate_table',{'duplicate.field':'customer_id','output.format':'json'},[READ,step('deduplicate','filter_sort'),OUT],missing={'duplicate_policy':['첫','마지막','남길','보존']},answers=[answer('duplicate_policy',['첫','마지막','남길','보존'],'중복은 마지막 행만 남겨줘.')],final_slots={'duplicate.field':'customer_id','duplicate.policy':'last','output.format':'json'})
add('multiturn','연결한 표에서 중복이면 첫 행만 남기고 JSON으로 저장해줘. 빈 값은 오류.','CLARIFY','deduplicate_table',{'duplicate.policy':'first','output.format':'json'},[READ,step('deduplicate','filter_sort'),OUT],missing={'duplicate_field':['기준','열','항목']},answers=[answer('duplicate_field',['기준','열','항목'],'중복 기준 열은 customer_id야.')],final_slots={'duplicate.field':'customer_id','duplicate.policy':'first'})
add('multiturn','한국 시간으로 매일 표를 JSON으로 저장해줘.','CLARIFY','schedule_table',{'schedule.frequency':'daily','schedule.timezone':'Asia/Seoul','output.format':'json'},[READ,OUT],missing={'hour':['몇 시','시간','시각']},answers=[answer('hour',['몇 시','시간','시각'],'매일 오전 9시 정각으로 해줘.')],final_slots={'schedule.frequency':'daily','schedule.hour':9,'schedule.minute':0,'schedule.timezone':'Asia/Seoul'})
add('multiturn','한국 시간으로 매주 오전 9시에 표를 JSON으로 저장해줘.','CLARIFY','schedule_table',{'schedule.frequency':'weekly','schedule.hour':9,'schedule.timezone':'Asia/Seoul'},[READ,OUT],missing={'weekday':['요일']},answers=[answer('weekday',['요일'],'매주 월요일로 해줘.')],final_slots={'schedule.frequency':'weekly','schedule.hour':9,'schedule.weekday':0})
add('multiturn','한국 시간으로 매월 오전 9시에 표를 JSON으로 저장해줘.','CLARIFY','schedule_table',{'schedule.frequency':'monthly','schedule.hour':9,'schedule.timezone':'Asia/Seoul'},[READ,OUT],missing={'monthday':['날짜','일자','며칠','말일']},answers=[answer('monthday',['날짜','일자','며칠','말일'],'매월 15일로 해줘.')],final_slots={'schedule.frequency':'monthly','schedule.hour':9,'schedule.monthday':15})
add('multiturn','연결한 표의 qty에 곱할 배율을 아직 정하지 못했어. scaled라는 열을 만들고 JSON으로 저장해줘.','CLARIFY','compute_table',{'compute.output_column':'scaled','output.format':'json'},[READ,step('compute','calculate_columns'),OUT],missing={'factor':['배율','곱','수식','값']},answers=[answer('factor',['배율','곱','수식','값'],'qty에 2를 곱해줘.')],final_slots={'compute.formula':'qty * 2','compute.output_column':'scaled'})
add('multiturn','연결한 표의 qty에 2를 곱한 새 열을 만들어 JSON으로 저장해줘. 새 열 이름은 아직 미정이야.','CLARIFY','compute_table',{'compute.formula':'qty * 2','output.format':'json'},[READ,step('compute','calculate_columns'),OUT],missing={'column':['열 이름','이름']},answers=[answer('column',['열 이름','이름'],'새 열 이름은 doubled로 해줘.')],final_slots={'compute.formula':'qty * 2','compute.output_column':'doubled'})
add('multiturn','연결한 표를 region별로 금액 합계 집계해 JSON으로 저장해줘. 금액 열은 아직 정하지 않았어. 빈 값은 오류로 처리해.','CLARIFY','aggregate_table',{'aggregate.group_by':['region'],'output.format':'json'},[READ,step('aggregate','aggregate_table'),OUT],missing={'sum_field':['금액','합계','열']},answers=[answer('sum_field',['금액','합계','열'],'합계할 금액 열은 amount야.')],final_slots={'aggregate.group_by':['region'],'aggregate.sum_field':'amount'})
add('multiturn','Gmail로 제목 점검, 본문 완료인 이메일을 보내줘.','CLARIFY','send_email',{'subject':'점검','body':'완료'},[step('send','email.send','execute')],missing={'recipient':['받는','수신','주소']},answers=[answer('recipient',['받는','수신','주소'],'수신자는 audit@example.invalid야.')],final_slots={'recipient':'audit@example.invalid','subject':'점검','body':'완료'},resources=None)
add('multiturn','Gmail에서 최근 5개 메일을 요약해줘.','READY','summarize_email',{'gmail.limit':5},MAIL,revision={'text':'아니, 10개로 바꿔줘.','reference_time':'2026-11-10T01:01:00Z'},final_slots={'gmail.limit':10},resources=None)
add('multiturn','Gmail에서 어제 온 메일을 요약해줘.','READY','summarize_email',{'gmail.period':'yesterday','gmail.date':'2026-11-09'},MAIL,revision={'text':'최대 5개만 요약해줘.','reference_time':'2026-11-11T01:00:00Z'},final_slots={'gmail.limit':5,'gmail.date':'2026-11-09'},resources=None)
# Unsupported is grounded in the absence of a registered action, not lack of an account.
add('unsupported','결과를 Slack 채널로 보내줘.','UNSUPPORTED','send_slack',{},unsupported=['Slack 전송'],resources=None)
add('unsupported','매출 데이터를 분석해서 이메일로 보내줘. 데이터는 연결된 고객 PostgreSQL 데이터베이스에서 직접 가져와.','UNSUPPORTED','database_sales_email',{'source':'connected_postgresql'},unsupported=['고객 DB 직접 조회'],resources=None)
cases[-1]['prior_conversation']=[{'role':'user','content':'매출 데이터를 분석해서 이메일로 보내줘.'},{'role':'assistant','content':'매출 데이터는 어디에서 가져오면 될까요?'}]
cases[-1]['request']='연결된 고객 PostgreSQL 데이터베이스에서 직접 가져와.'
cases[-1]['resource_state']['customer_db_connection_claim']='synthetic_metadata_only; no registered DB query tool'
add('unsupported','한국 시간으로 매년 12월 31일 오전 9시에 표를 JSON으로 저장해줘. 다른 주기는 안 돼.','UNSUPPORTED','yearly_table',{'schedule.frequency':'yearly'},unsupported=['연간 예약'])
add('unsupported','결과를 Microsoft Teams 채널로 보내줘.','UNSUPPORTED','send_teams',{},unsupported=['Teams 전송'],resources=None)
add('unsupported','WhatsApp으로 고객에게 알림을 발송해줘.','UNSUPPORTED','send_whatsapp',{},unsupported=['WhatsApp 전송'],resources=None)
# Seven composite tasks; partial supported work must not conceal an unsupported action.
add('composite','매일 오후 6시에 Gmail로 받은 고객 문의를 분석해서 중요 내용을 요약하고 Notion에 정리해줘.','UNSUPPORTED','scheduled_gmail_notion',{'schedule.frequency':'daily','schedule.hour':18,'provider':'gmail'},MAIL,unsupported=['Notion 저장'],resources=None)
add('composite','연결한 표에서 qty * unit_price로 total 열을 만들고 total이 20 이상인 행만 total 숫자 내림차순으로 정렬해서 JSON으로 저장해줘.'+POLICY,'READY','compute_filter_sort_export',{'compute.formula':'qty * unit_price','compute.output_column':'total','filter.field':'total','filter.operator':'gte','filter.value':20,'sort.direction':'descending','output.format':'json'},[READ,step('compute','calculate_columns'),FILTER,SORT,OUT],rows=[{**TABLE['rows'][1],'total':30},{**TABLE['rows'][2],'total':20}])
add('composite','연결한 표를 region별 amount 합계로 집계하고 sum이 25 이상인 결과만 JSON으로 저장해줘.'+POLICY,'READY','aggregate_filter_export',{'aggregate.group_by':['region'],'aggregate.sum_field':'amount','filter.field':'sum','filter.operator':'gte','filter.value':25},[READ,step('aggregate','aggregate_table'),FILTER,OUT])
add('composite','연결한 정책 문서를 요약하고 Markdown 파일로 저장해줘.','READY','summarize_export_document',{'output.format':'md'},[step('read','read_document'),step('summary','compose_answer'),step('output','export_result')],resources='document')
add('composite','연결한 표에서 customer_id 중복은 첫 행만 남긴 후 amount가 15 이상인 행을 JSON으로 저장해줘. 빈 값은 오류.','READY','deduplicate_filter_export',{'duplicate.field':'customer_id','duplicate.policy':'first','filter.field':'amount','filter.operator':'gte','filter.value':15},[READ,step('deduplicate','filter_sort'),FILTER,OUT])
add('composite','Gmail에서 최근 3개 메일을 요약하고 audit@example.invalid로 제목 요약 결과인 이메일을 보내줘.','READY','summarize_send_email',{'gmail.limit':3,'recipient':'audit@example.invalid','subject':'요약 결과'},MAIL+[step('send','email.send','execute')],resources=None)
add('composite','연결한 표에서 amount가 큰 행만 선별한 뒤 region별 amount 합계를 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.','CLARIFY','filter_aggregate_export',{'filter.field':'amount','aggregate.group_by':['region'],'aggregate.sum_field':'amount','output.format':'json'},[READ,FILTER,step('aggregate','aggregate_table'),OUT],missing={'threshold':['기준','얼마','값'],'operator':['포함','초과','이상']})

catalog = {t['id']: t for t in TOOLS}
for case in cases:
    for task in case['gold']['tools_and_operations']:
        for option in task['alternatives']:
            assert option['tool'] in catalog, option
            operations = [o.get('operation') or o.get('id') or o.get('name') for o in catalog[option['tool']].get('operation_contracts', [])]
            assert option['operation'] is None or option['operation'] in operations, option
payload = {'version': 1, 'source': 'manually authored synthetic gold; frozen before any inference',
           'catalog_fingerprint': catalog_fingerprint(), 'categories': {g:sum(c['category']==g for c in cases) for g in ('ready','clarify','temporal','multiturn','unsupported','composite')},
           'execution_policy': 'Never access Gmail/Notion/customer DB or send mail. Only allowlisted local table workflows may execute.',
           'question_policy': 'Answer only an emitted matching question. Unmatched or ambiguous question requires adjudication; do not guess.',
           'cases': cases,
           'excluded': [{'id':'AMB01','request':'그 자료를 적당히 처리해줘.','reason':'지시 대상·업무가 없고 지원성 판단 정답을 단일 클래스로 확정할 수 없음'}]}
payload['gold_sha256'] = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
if __name__ == '__main__':
    path = ROOT / 'docs/qwen-live-evaluation-cases.json'
    if path.exists(): raise SystemExit('Gold already exists. Do not replace a frozen evaluation corpus.')
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'count':len(cases),'categories':payload['categories'],'gold_sha256':payload['gold_sha256']}))
