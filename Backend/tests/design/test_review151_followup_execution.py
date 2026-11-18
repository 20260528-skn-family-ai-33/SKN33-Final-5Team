"""Kit T14 output changes must reach the actual persisted workflow and artifact."""
import base64
import csv
import io
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('replies, final_format', [
    (['네.'], 'csv'),
    (['같은 열을 JSON으로 저장해줘.', '네.'], 'json'),
    (['CSV로 저장하지 말고 JSON으로 저장해줘.', '네.', 'CSV로 저장해줘.'], 'csv'),
    (['CSV 저장은 취소해줘.', '네.', 'CSV로 저장해줘.', '네.'], 'csv'),
])
def test_t14_clarification_followups_save_execute(replies, final_format, tmp_path, monkeypatch):
    import service.handlers.design as design
    from service.worker import handle
    from agent_builder.design.export_intent import explicit_export_intent
    from tests.design.test_feedback_service_scenarios import ORDER_REQUEST, ORDER_ANSWER, order_specs, order_draft, UnexpectedConditionCall
    source = Path(__file__).parents[1] / 'fixtures/manual_kit/orders.csv'
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream); columns, rows = reader.fieldnames, list(reader)
    # Independent oracle from the original 12 source rows and the kit's answer.
    expected = sorted([{'주문번호':r['주문번호'], '주문금액':int(r['수량'])*int(r['단가'])}
        for r in rows if r['주문상태'] == '완료' and int(r['수량'])*int(r['단가']) >= 100000],
        key=lambda r:r['주문번호'])
    build = design.build_graph
    identifier = None
    class Capability:
        def invoke(self, messages):
            return dict(content=json.dumps(dict(decision='existing_tools', reason='표 처리',
                tool_ids=['read_table','calculate_columns','filter_sort','json_transform','export_result'],
                missing_conditions=[],missing_actions=[],user_quotes=[ORDER_REQUEST])))
    class Requirements:
        def invoke(self, messages):
            payload = json.loads(messages[1]['content'])
            if len(payload['user_messages']) == 1:
                raw = dict(tasks=[dict(source_message=0,description='저장',spec=dict(kind='export',format='csv'))],
                    unresolved=[dict(key='filter.value',text='계산식과 높은 주문의 기준을 알려주세요.')])
            else:
                intent = explicit_export_intent(dict(messages=payload['conversation']))
                specs = order_specs(identifier)[:-1]
                specs += [dict(kind='export',format=f) for f in sorted(intent['formats'])]
                raw = dict(tasks=[dict(source_message=1,description=s['kind'],spec=s) for s in specs],unresolved=[])
            return dict(content=json.dumps(raw))
    monkeypatch.setattr(design,'build_graph',lambda *a,**kw:build(*a,**{**kw,
        'capability_mode':'enforce','capability_transport':Capability(), 'requirement_mode':'enforce',
        'requirement_transport':Requirements(),'condition_mode':'enforce',
        'condition_transport':UnexpectedConditionCall(),'verifier_mode':'off'}))
    base = dict(directory=str(tmp_path))
    result = handle({**base,'action':'new_session','model':'fixture','credential_id':'fixture'}); sid=result['id']
    result = handle({**base,'action':'upload','id':sid,'revision':0,
        'asset':dict(name='주문내역.csv',kind='table',format='csv',columns=columns),'rows':rows})
    identifier = result['resources'][0]['id']
    def conversation(current):
        draft = order_draft(identifier)
        intent = explicit_export_intent(current)
        nodes = draft['workflow']['nodes']
        if not intent['formats']:
            nodes.pop(); draft['workflow']['edges'].pop()
        else:
            fmt = next(iter(intent['formats']))
            if fmt == 'json':
                nodes[-1]['settings']['operation'] = 'write_json'
                nodes[-1]['settings']['arguments_json'] = json.dumps(dict(path='고액주문.json'))
                nodes[-1]['bindings'][0]['input'] = 'value'
        draft['tool_ids'] = list(dict.fromkeys(n['tool'] for n in nodes))
        return dict(draft=draft, repair_count=current.get('repair_count', 0) + 1)
    def send(text, **extra):
        return handle({**base,'action':'message','id':sid,'revision':result['revision'],'content':text,**extra}, conversation=conversation)
    result = send(ORDER_REQUEST)
    assert result['status'] == 'needs_clarification'
    result = send(ORDER_ANSWER,question_id=result['pending_questions'][0]['id'])
    assert result['status'] == 'ready', result.get('issues')
    for reply in replies:
        result = send(reply)
        assert result['status'] == 'ready', result.get('issues')
    result = handle({**base,'action':'confirm','id':sid,'revision':result['revision']})
    assert result['status'] == 'saved',result.get('issues')
    run = handle({**base,'action':'test','id':result['saved_agent_id'],'content':''})
    assert run['status'] == 'succeeded',run.get('error')
    content = base64.b64decode(run['artifact']['content']).decode('utf-8-sig')
    assert run['artifact']['name'].endswith('.'+final_format)
    if final_format == 'json':
        assert json.loads(content) == expected
    else:
        reader = csv.DictReader(io.StringIO(content))
        assert reader.fieldnames == ['주문번호','주문금액']
        assert list(reader) == [{k:str(v) for k,v in r.items()} for r in expected]
