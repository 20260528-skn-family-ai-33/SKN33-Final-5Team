"""Compile ordered business steps into the service graph without inventing tools."""
from copy import deepcopy

INSTRUCTIONS='''
현재 생성 인코딩은 ordered_business_steps입니다. workflow에는 first_step과 next_steps만 씁니다.
first_step은 tool, label, settings만 작성하며 앞 단계가 없으므로 bindings를 작성하지 않습니다. 사용자 지정 값은 settings의 상수 인자입니다.
첨부 자료를 처리할 때는 먼저 read_table/read_document로 실제 파일을 읽습니다. 두 첨부 표를 결합하면 읽기 단계 두 개를 각각 만들고 join의 left/right를 연결합니다. 첫 단계에 $binding을 쓰거나 첨부 표를 상수 rows로 복사하지 않습니다.
next_steps는 뒤따르는 단계 목록이며 각 단계는 tool, label, settings, bindings를 작성합니다.
생성 스키마가 next_steps를 객체로 지정하면 step_2, step_3 등의 필수 번호 항목을 모두 작성합니다. 앱이 이를 같은 순서의 목록으로 변환하며 단계의 내용은 바꾸지 않습니다.
current_draft는 저장용 그래프 표현일 수 있지만 새 응답은 현재 생성 스키마를 사용합니다.
노드 id와 실행 edges는 앱이 순서대로 만듭니다.
bindings는 input=실제 인자/입력 포트명, step=참조할 앞 단계의 1부터 시작하는 번호, output=그 단계의 실제 출력 포트입니다.
예: 두 읽기 단계 다음 join의 bindings=[{"input":"left","step":1,"output":"table"},{"input":"right","step":2,"output":"table"}].
상수 인자는 settings.arguments_json에만 쓰며 같은 인자를 binding에도 중복 작성하지 않습니다.
명시한 단계만 실행합니다. 필요한 도구를 빠뜨리지 말고 화면 표시용 중복 계산·파싱 단계를 추가하지 않습니다.
finish는 필요할 때 마지막에 한 번만 둡니다. 질문·미지원이면 workflow=null입니다.
condition을 조회해 분기가 필요해지면 이후 생성 스키마는 기존 entry/nodes/edges 그래프로 전환됩니다.
'''


def schema_for(schema):
    result=deepcopy(schema)
    node_limits=result['$defs']['WorkflowDefinition']['properties'].get('nodes',{})
    minimum=max(0,node_limits.get('minItems',1)-1)
    maximum=min(14,node_limits.get('maxItems',15)-1)
    def convert(value):
        if isinstance(value,dict):
            properties=value.get('properties',{})
            if {'input','node_id','output'}<=properties.keys():
                properties.pop('node_id')
                properties['step']={'type':'integer','minimum':1,'maximum':15}
                value['required']=['input','step','output']
            for child in value.values():convert(child)
        elif isinstance(value,list):
            for child in value:convert(child)
    variants=result['$defs']['WorkflowNode']['anyOf']
    for variant in variants:
        variant['properties'].pop('id',None)
        variant['required']=[k for k in variant['required'] if k!='id']
    convert(result)
    first=[]
    for candidate in variants:
        # The first step has no predecessors. Dropping its bindings field used
        # to also drop the native minItems requirement, making an inputless
        # filter/compute or an unbound join a decoder-valid first step.
        if candidate['properties']['bindings'].get('minItems',0)>0:continue
        variant=deepcopy(candidate)
        settings=variant['properties']['settings']['properties']
        operation=settings.get('operation',{}).get('enum',[])
        identifiers=variant['properties']['tool'].get('enum',[])
        arguments=settings.get('arguments_json',{})
        if len(operation)==len(identifiers)==1 and arguments.get('type')=='object':
            from workflow_engine.catalog.canonical import arguments_spec, operation_spec
            required=arguments_spec(identifiers[0],operation_spec(identifiers[0],operation[0]))
            arguments['required']=[name for name,needed in required.items() if needed]
            # Constant Tables are valid even in the first step. A $binding is
            # not: there cannot be an earlier source to resolve it against.
            for prop in arguments.get('properties',{}).values():
                if 'anyOf' in prop:
                    prop['anyOf']=[option for option in prop['anyOf']
                                  if '$binding' not in option.get('properties',{})]
        variant['properties'].pop('bindings',None)
        variant['required']=[k for k in variant['required'] if k!='bindings']
        first.append(variant)
    result['$defs']['FirstBusinessStep']={'anyOf':first}
    result['$defs']['WorkflowDefinition']={'type':'object','additionalProperties':False,
        'properties':{'first_step':{'$ref':'#/$defs/FirstBusinessStep'},
                      'next_steps':{'type':'array','minItems':minimum,'maxItems':maximum,'items':{'$ref':'#/$defs/WorkflowNode'}}},
        'required':['first_step','next_steps']}
    return result


def compile_workflow(workflow):
    """Assign IDs and linear execution edges; all argument/port checks still follow."""
    if isinstance(workflow,dict) and set(workflow)=={'first_step','next_steps'}:
        first=workflow['first_step'];following=workflow['next_steps']
        if isinstance(following,dict):
            keys=['step_'+str(i) for i in range(2,len(following)+2)]
            if not following or set(following)!=set(keys):
                raise ValueError('번호형 업무 단계는 step_2부터 빠짐없이 연결해야 합니다.')
            following=[following[k] for k in keys]
        if not isinstance(first,dict) or set(first)!={'tool','label','settings'} or not isinstance(following,list):
            raise ValueError('첫 단계에는 tool·label·settings만 쓰고 next_steps는 목록으로 작성하세요.')
        steps=[{**first,'bindings':[]},*following]
    elif isinstance(workflow,dict) and set(workflow)=={'steps'}:steps=workflow['steps']
    else:raise ValueError('순서형 워크플로우에는 first_step과 next_steps를 지정하세요.')
    if not isinstance(steps,list) or not 1<=len(steps)<=15:
        raise ValueError('업무 단계는 1~15개여야 합니다.')
    nodes=[]
    for index,step in enumerate(steps,1):
        if not isinstance(step,dict) or set(step)!={'tool','label','settings','bindings'}:
            raise ValueError('단계에는 tool·label·settings·bindings가 필요합니다.')
        if step['tool']=='condition':raise ValueError('조건 분기는 기존 그래프 인코딩으로 설계하세요.')
        if step['tool']=='finish' and index!=len(steps):raise ValueError('finish는 마지막 단계에만 둘 수 있습니다.')
        bindings=[]
        if not isinstance(step['bindings'],list):raise ValueError('bindings는 목록이어야 합니다.')
        for binding in step['bindings']:
            if not isinstance(binding,dict) or set(binding)!={'input','step','output'}:
                raise ValueError('단계 연결에는 input·step·output이 필요합니다.')
            previous=binding['step']
            if type(previous) is not int or not 1<=previous<index:
                raise ValueError(f'{index}단계의 {binding["input"]} 입력은 step={previous!r}를 참조했습니다. 앞 단계 번호 1~{index-1}만 참조할 수 있습니다. 첫 단계에는 bindings가 없습니다.')
            bindings.append({'input':binding['input'],'node_id':f'step_{previous}','output':binding['output']})
        nodes.append({'id':f'step_{index}','tool':step['tool'],'label':step['label'],
                      'settings':deepcopy(step['settings']),'bindings':bindings})
    return {'entry':'step_1','nodes':nodes,
            'edges':[{'source':a['id'],'target':b['id'],'branch':'next'} for a,b in zip(nodes,nodes[1:])]}
