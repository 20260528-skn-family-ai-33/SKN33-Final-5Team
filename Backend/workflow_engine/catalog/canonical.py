"""Operation contracts shared by planner, validator, reusable nodes and executor.

The 56 business IDs come from the supplied SVM catalogue. Existing control nodes
and write_result remain compatibility tools; they are not extra ML labels.
"""
import inspect
import json
import re
from pathlib import Path

BUSINESS_TOOLS = json.loads(Path(__file__).with_name('business56.json').read_text(encoding="utf-8"))
BUSINESS_IDS = tuple(t['id'] for t in BUSINESS_TOOLS)
CONTROL_IDS = ('agent_node', 'finish', 'condition', 'ask_user', 'saved_workflow', 'write_result')
TOOL_IDS = tuple(dict.fromkeys((*BUSINESS_IDS, *CONTROL_IDS)))
CATALOG = json.loads((Path(__file__).parents[1] / 'unified_tools/catalog.json').read_text(encoding="utf-8"))
OPERATIONS = {t['id']: [o for o in t['operations'] if not o.get('backend_tool')]
              for t in CATALOG['tools']}


def operation_spec(tool, operation):
    options = OPERATIONS.get(tool, [])
    if operation is None and len(options) == 1:
        return options[0]
    found = next((o for o in options if o['id'] == operation), None)
    if found is None:
        raise ValueError(f'{tool}: operation을 지정하세요. 지원 동작: ' + ', '.join(o['id'] for o in options))
    return found


def arguments_spec(tool, spec):
    # Function signatures are the executable source of required/optional arguments.
    from workflow_engine.unified_tools.local_operations import OPERATIONS as local
    from workflow_engine.unified_tools.model_operations import SPECS
    from workflow_engine.unified_tools.expansion.runtime import TOOLS
    entry = local.get((tool, spec['id']))
    fn = entry['function'] if entry else TOOLS[spec['runtime_id']]['function'] if spec.get('runtime_id') else None
    if fn:
        return {name: p.default is inspect.Parameter.empty for name, p in inspect.signature(fn).parameters.items()
                if name != 'context'}
    if (tool, spec['id']) in SPECS:
        required = SPECS[(tool, spec['id'])][2]
        return {name: True for name in required} | ({'glossary': False} if tool == 'translate_document' else {})
    if tool == 'check_document_links':
        return {'document': True, 'timeout_seconds': True, 'credential_ref': False}
    if spec.get('implementation') == 'keyword_search':
        return {'documents': True, 'keywords': True, 'top_k': False}
    if spec.get('implementation') == 'export_serialize':
        return {'value': True, 'format': True, 'filename': True}
    raise ValueError('실행 인자 계약이 없는 동작: ' + tool + '.' + spec['id'])


def parse_arguments(settings):
    raw = settings.get('arguments_json') or '{}'
    def reject(value):
        raise ValueError('JSON 인자에 유한한 값만 사용할 수 있습니다: ' + value)
    value = json.loads(json.dumps(raw,allow_nan=False),parse_constant=reject) if isinstance(raw,dict) else json.loads(raw, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError('arguments_json은 JSON 객체여야 합니다.')
    if 'context' in value:
        raise ValueError('실행 context는 서버가 제공합니다.')
    return value


def ports(tool, spec, selected=None):
    args = arguments_spec(tool, spec)
    # JSON inputs explicitly accept typed predecessor values. No guessed conversion.
    inputs = {name: 'json' for name in args}
    for name in ('table', 'left', 'right', 'baseline', 'candidate'):
        if name in inputs and (tool in ('join_tables', 'compare_tables') or name == 'table'):
            inputs[name] = 'table'
    if spec.get('runtime_id')=='table.join':
        inputs.update(left='records',right='records')
    if (spec.get('runtime_id') or '').startswith('table.') and 'rows' in inputs:
        inputs['rows']='records'
    if spec.get('runtime_id')=='file.write_csv' and 'rows' in inputs:inputs['rows']='records'
    for name in ('text', 'body', 'subject', 'url', 'timestamp', 'formula'):
        if name in inputs: inputs[name] = 'text'
    outputs = {'result': 'json', 'text': 'text'}
    declared = spec.get('outputs', '')
    if re.search(r'\btable:Table\b', declared): outputs['table'] = 'table'
    if re.search(r'\bdocument:Document\b', declared): outputs.update(document='json', documents='documents', text='text')
    if re.search(r'\bartifact:Artifact\b', declared): outputs.update(asset='json', artifact='artifact')
    if re.search(r'\bpages:PageImage\[\]', declared): outputs['pages'] = 'json'
    if spec.get('runtime_id') in ('table.join', 'table.filter', 'table.sort', 'table.select_columns', 'table.cast', 'table.calculate', 'table.aggregate', 'file.read_csv'):
        outputs['records'] = 'records'
    if tool=='export_result' and spec['id'] in ('serialize','write_json','write_csv'):outputs.update(asset='json',artifact='artifact')
    if spec.get('runtime_id') in ('url.build',): outputs['text'] = 'text'
    if selected in ('table','documents','records','text'):outputs[selected]=selected
    return inputs, outputs


# Scalar policies whose domain must be checked before saving, even when table
# inputs will only be supplied later. The executor still validates actual data.
ARGUMENT_CONSTRAINTS = {
 ('join_tables','join'):{'keys':{'type':'array','items':'string','min_items':1},'how':{'enum':['inner','left']},
     'cardinality':{'enum':['one_to_one','many_to_one']},'null_keys':{'enum':['error']},'max_rows':{'type':'positive_integer'}},
 ('join_tables','join_records'):{'keys':{'type':'array','items':'string','min_items':1},
     'how':{'enum':['inner','left']},'cardinality':{'enum':['one_to_one','many_to_one']}},
 ('compare_tables','diff'):{'keys':{'type':'array','items':'string','min_items':1}},
 ('scalar_calculate','evaluate'):{'formula':{'type':'string'},'variables':{'type':'object'}},
 ('url_transform','parse'):{'url':{'type':'string'}},
 ('create_spreadsheet','create'):{'sheets':{'type':'array','items':'object'}},
}


def validate_constant_domains(tool,operation,args,placeholder_names):
    for key,rule in ARGUMENT_CONSTRAINTS.get((tool,operation),{}).items():
        if key not in args or placeholder_names(args[key]):continue
        value=args[key];kind=rule.get('type')
        valid=(kind is None or kind=='array' and isinstance(value,list) or kind=='object' and isinstance(value,dict)
               or kind=='string' and isinstance(value,str) or kind=='positive_integer' and type(value) is int and value>0)
        if 'enum' in rule:valid=value in rule['enum']
        if valid and rule.get('items')=='string':valid=all(isinstance(v,str) for v in value)
        if valid and rule.get('items')=='object':valid=all(isinstance(v,dict) for v in value)
        if valid and 'min_items' in rule:valid=len(value)>=rule['min_items'] and all(v.strip() for v in value)
        if not valid:raise ValueError(key+' 실행 인자의 허용 규격: '+json.dumps(rule,ensure_ascii=False))
    if (tool,operation)==('create_spreadsheet','create') and 'sheets' in args:
        sheets=args['sheets']
        if isinstance(sheets,dict) and placeholder_names(sheets):return
        if not isinstance(sheets,list) or not sheets:raise ValueError('sheets에는 name·table이 있는 시트를 한 개 이상 지정하세요.')
        names=set()
        for sheet in sheets:
            if not isinstance(sheet,dict) or not isinstance(sheet.get('name'),str) or not sheet['name'].strip() or 'table' not in sheet:
                raise ValueError('각 시트에는 name과 table이 필요합니다. table은 columns·rows를 가진 객체입니다.')
            if sheet['name'] in names:raise ValueError('시트 이름이 중복됩니다: '+sheet['name'])
            names.add(sheet['name'])
            table=sheet['table']
            if placeholder_names(table):continue
            if not isinstance(table,dict) or not isinstance(table.get('rows'),list) or any(not isinstance(r,dict) for r in table['rows']):
                raise ValueError('시트 table.rows에는 열 이름을 키로 하는 행 객체 목록이 필요합니다.')
            columns=table.get('columns') or list(dict.fromkeys(c for row in table['rows'] for c in row))
            if not isinstance(columns,list) or any(not isinstance(c,str) or not c for c in columns) or len(set(columns))!=len(columns):
                raise ValueError('시트 table.columns는 중복 없는 열 이름 목록이어야 합니다.')
            for row in table['rows']:
                if set(row)-set(columns):raise ValueError('시트 행에 columns에 없는 열이 있습니다.')
                if any(isinstance(value,(dict,list)) for value in row.values()):raise ValueError('엑셀 셀에는 단일 값만 지정하세요.')


def contract(tool, spec):
    inputs, outputs = ports(tool, spec)
    args = arguments_spec(tool, spec)
    scope=spec.get('scope', spec.get('inputs', ''))
    if spec.get('runtime_id'):
        from workflow_engine.unified_tools.expansion.runtime import TOOLS
        scope=TOOLS[spec['runtime_id']]['description']
    return {'id': spec['id'], 'arguments': [{'name': k, 'required': v} for k, v in args.items()],
            'input_ports': inputs, 'output_ports': outputs, 'scope':scope,
            'input_contract': spec.get('inputs', ''), 'argument_constraints':ARGUMENT_CONSTRAINTS.get((tool,spec['id']),{}), 'status': spec['status'],
            'side_effect': spec.get('side_effect', False)}


PLANNER_INSTRUCTIONS = '''
56개 업무 도구와 제어·호환 도구는 supported_tools의 실제 계약을 따릅니다.
이 앱에서 "화면에 보여줘", "결과를 출력해줘"는 실행 결과 영역에 text/표를 표시한다는 뜻입니다. 화면 종류·웹 브라우저·외부 시스템 중 선택하라고 다시 묻지 않습니다. 사용자가 외부 시스템을 명시한 경우만 별도 도구·연결을 확인합니다. 단순 계산은 formula와 variables가 주어졌으면 즉시 설계하며 출력용 파일이나 추가 역할이 필요하지 않습니다.
create_spreadsheet.create의 sheets는 [{"name":"Data","table":{"columns":["id","amount"],"rows":[{"id":"A","amount":10}]}}] 형태입니다. 시트의 table 안에 columns와 rows를 지정하며 rows는 열 이름을 키로 하는 행 객체 목록입니다.
scalar_calculate.evaluate는 수식의 모든 변수 값을 variables에 지정합니다. 예: formula="x*3+2", variables={"x":7}. 수식에 x가 있는데 variables={}이면 실행할 수 없습니다.
selected_tool_contracts가 제공되면 그 실제 동작 계약을 사용합니다. 제공되지 않은 확장 도구는 inspect_tool로 operation_contracts를 조회합니다.
확장 동작은 settings.operation에 동작 ID,
settings.arguments_json에 상수 인자의 JSON 객체 문자열을 지정합니다. 앞 단계 값은
bindings의 input=해당 인자명으로 연결하며 상수 인자와 중복하지 않습니다.
입력 상수가 모두 지정된 단순 계산·URL 분석은 해당 동작 노드 하나만으로 종료할 수 있습니다. 같은 동작을 화면 표시용으로 한 번 더 연결하지 않습니다.
모든 확장 동작은 result와 text를 기본 출력합니다. 화면 표시는 실행기가 하므로 표시만을 위한 추가 변환 노드가 필요하지 않습니다. output_port는 JSON/목록 선택 결과의 자료형을 선언할 때만 지정하고 나머지는 null입니다.
result는 원시 JSON 출력입니다. table/documents/text/asset/artifact 등은 동작별
output_ports에 있는 것만 사용합니다. file_ids는 이번 대화의 실제 자료 ID만 씁니다.
파일 인자는 {"asset_id":"실제 자료 ID"}로 지정하거나 앞 단계 asset을 연결합니다.
입출력의 경로 선택이 필요하면 json_transform.lookup 또는 list_transform.select를
명시적으로 연결합니다. JSON에서 표를 선택하면 settings.output_port="table"로 결과 자료형을 선언합니다. 없는 경로·항목·계정 연결은 추측하지 않습니다.
목록·객체 안에 앞 결과를 넣어야 하면 arguments_json의 해당 위치에 {"$binding":"입력 인자명"}을 쓰고 같은 이름의 binding을 연결합니다. 예: create_spreadsheet의 sheets=[{"name":"Result","table":{"$binding":"sheets"}}], binding.input=sheets.
기존 22개 도구는 operation=null이면 기존 settings와 기존 포트를 그대로 씁니다. 업로드 CSV 두 개를 연결할 때는 각각 별도의 read_table(operation=null, file_ids=[각 실제 파일 ID])로 table을 읽고 join_tables의 left/right 입력에 연결합니다. 일반 실행 순서는 read_left → read_right → join → write_result이며 write_result는 join의 table을 받습니다.
Ollama 생성 규격에서 arguments_json이 객체이면 문자열로 감싸지 말고 실제 JSON 객체로 작성합니다. 저장 시 앱이 문자열로 정규화합니다.
계정 연결이 필요한 도구도 등록된 기능입니다. 연결 필요와 도구 미구현을 구분하고,
Slack 등 등록되지 않은 공급자를 Gmail로 대체하지 않습니다.
'''


def operation_variants(node_schema, binding_schema, tool_ids=None, variable_names=()):
    """Shared compact grammar; exact per-operation contracts are checked before save."""
    from copy import deepcopy
    args = sorted({name for tool,specs in OPERATIONS.items() for spec in specs for name in arguments_spec(tool,spec)})
    variant = deepcopy(node_schema)
    variant['properties']['tool'] = {'type':'string','enum':list(BUSINESS_IDS)}
    variant['properties']['settings'] = {'type':'object','additionalProperties':False,
        'properties':{'operation':{'type':'string','enum':sorted({s['id'] for specs in OPERATIONS.values() for s in specs})},
                      'arguments_json':{'type':'string','description':'JSON object of constant arguments; predecessor values use bindings.'}},
        'required':['operation','arguments_json']}
    variant['properties']['settings']['properties']['output_port']={'anyOf':[{'type':'null'},{'type':'string','enum':['table','documents','records','text']}]}
    variant['properties']['settings']['required'].append('output_port')
    import os
    if os.getenv('A4B_MAIN_PROVIDER')=='ollama':
        variant['properties']['settings']['properties']['arguments_json']={'type':'object','additionalProperties':True,'description':'실행 인자 객체. 문자열로 다시 인코딩하지 마세요.'}
    binding = deepcopy(binding_schema)
    binding['properties']['input'] = {'type':'string','enum':args}
    variant['properties']['bindings'] = {'type':'array','items':binding,'maxItems':20}
    from copy import deepcopy
    projection=deepcopy(variant)
    projection['properties']['tool']={'type':'string','enum':['json_transform','list_transform']}
    variant['properties']['settings']['properties']['output_port']={'type':'null'}
    if os.getenv('A4B_MAIN_PROVIDER')=='ollama':
        # Give the decoder each tool's actual argument and binding names. A
        # shared union lets small models put another tool's keys into a node.
        variants=[]
        for identifier,specs in OPERATIONS.items():
            if tool_ids is not None and identifier not in tool_ids:continue
            for spec in specs:
                typed=deepcopy(projection if identifier in ('json_transform','list_transform') else variant)
                typed['properties']['tool']={'type':'string','enum':[identifier]}
                settings=typed['properties']['settings']['properties']
                settings['operation']={'type':'string','enum':[spec['id']]}
                names=sorted(arguments_spec(identifier,spec))
                properties={name:{} for name in names}
                for name,rule in ARGUMENT_CONSTRAINTS.get((identifier,spec['id']),{}).items():
                    kind=rule.get('type');prop={}
                    if kind=='positive_integer':prop={'type':'integer','minimum':1}
                    elif kind:prop={'type':kind}
                    if rule.get('items'):
                        prop['items']={'type':rule['items']}
                        if rule['items']=='object':prop['items']['additionalProperties']=True
                    if 'enum' in rule:prop['enum']=rule['enum']
                    properties[name]=prop
                if identifier=='scalar_calculate':properties['variables']={'type':'object',
                    'properties':{name:{'type':'number'} for name in sorted(variable_names)},
                    'additionalProperties':{'type':'number'}}
                if (identifier,spec['id'])==('create_spreadsheet','create'):
                    cell={'anyOf':[{'type':'null'},{'type':'boolean'},{'type':'number'},{'type':'string'}]}
                    row={'type':'object','properties':{name:deepcopy(cell) for name in sorted(variable_names)},
                         'additionalProperties':deepcopy(cell)}
                    literal={'type':'object','properties':{
                        'columns':{'type':'array','items':{'type':'string'}},
                        'rows':{'type':'array','items':row}},'required':['columns','rows'],'additionalProperties':False}
                    reference={'type':'object','properties':{'$binding':{'type':'string'}},'required':['$binding'],'additionalProperties':False}
                    properties['sheets']={'type':'array','minItems':1,'items':{'type':'object',
                        'properties':{'name':{'type':'string'},'table':{'anyOf':[literal,reference]}},
                        'required':['name','table'],'additionalProperties':False}}
                settings['arguments_json']={'type':'object','properties':properties,'additionalProperties':False}
                binding=deepcopy(binding_schema)
                binding['properties']['input']={'type':'string','enum':names}
                typed['properties']['bindings']={'type':'array','items':binding,'maxItems':len(names)}
                variants.append(typed)
        return variants
    return [variant,projection]


def planner_catalog(tools, *, details=False):
    """Full ID discovery without repeating paths, versions and large descriptions."""
    result=[]
    for tool in tools:
        item={k:tool[k] for k in ('id','name')}
        item['description']=tool['description'][:120]
        # Union ports across different operations are not a node contract.
        # Native ports remain discoverable; exact operation ports are inspected.
        if tool.get('execution_status')=='native_ready' or not tool.get('operation_contracts'):
            item.update(input_ports=tool['input_ports'],output_ports=tool['output_ports'])
        contracts=tool.get('operation_contracts',[])
        if contracts:
            if details:item['operation_contracts']=contracts
            else:item['operations']=[o['id'] for o in contracts]
        result.append(item)
    return result
