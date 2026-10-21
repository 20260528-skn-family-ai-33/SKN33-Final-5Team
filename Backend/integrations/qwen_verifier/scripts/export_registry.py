"""확인한 저장소의 registry 상수만 AST로 읽는다. 프로젝트 코드를 실행하지 않는다."""
import ast,json,argparse,subprocess
from pathlib import Path
def export(repo,dest):
    src=repo/'Backend/agent_builder/registry.py';tree=ast.parse(src.read_text());ports={};items=[]
    for n in tree.body:
        if isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name) and t.id=='TOOLS':items.extend(ast.literal_eval(n.value))
                if isinstance(t,ast.Name) and t.id=='PORTS':ports.update(ast.literal_eval(n.value))
                if isinstance(t,ast.Subscript) and isinstance(t.value,ast.Name) and t.value.id=='PORTS':ports[ast.literal_eval(t.slice)]=ast.literal_eval(n.value)
        if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and isinstance(n.iter,ast.List):
            items.extend({'id':v[0],'description':v[2]} for v in ast.literal_eval(n.iter))
    required_ports={'filter_sort':['table'],'write_result':['table'],'aggregate_table':['table'],
        'search_document':['documents'],'extract_information':['documents'],'review_summary':['reviews']}
    required_settings={'read_table':['file_ids'],'read_document':['file_ids'],'filter_sort':['execution_plan'],
        'write_result':['format'],'aggregate_table':['fields'],
        'saved_workflow':['agent_id','agent_version','output_node','output_port']}
    tools=[]
    for x in items:
        tid=x['id'];ip,op=ports[tid]
        tools.append({'id':tid,'description':x['description'],'enabled':True,'input_ports':ip,'output_ports':op,
            'required_input_ports':required_ports.get(tid,[]),'required_settings':required_settings.get(tid,[]),
            'check_scope':'dynamic' if tid in ['agent_node','saved_workflow'] else 'table' if tid in ['read_table','filter_sort','aggregate_table','write_result'] else 'basic'})
    assert len(tools)==16 and len({t['id'] for t in tools})==16
    commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    dest.write_text(json.dumps({'registry_version':'a4b-57476c7-v1','source_commit':commit,
        'scope_note':'등록부 스냅샷입니다. 모든 도구의 실행·의미 검증 구현을 보장하지 않습니다. 필수 설정 검사는 연동 예제용 부분집합입니다.',
        'tools':tools},ensure_ascii=False,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();export(a.repo,a.output)
