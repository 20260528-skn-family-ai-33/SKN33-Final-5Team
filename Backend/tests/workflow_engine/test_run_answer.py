import json

import pytest

from infrastructure.persistence.storage import Storage
from workflow_engine.run_answer import public_run_answer


@pytest.mark.parametrize('wrapped', [True, False])
def test_existing_file_run_keeps_the_transcript_and_ports_in_storage(tmp_path, wrapped):
    asset = {'path':'generated/05a3ba26eab949f98796d5acb9b0d23f.xlsx',
             'version':'6b990c07383d96d0564d62c3aef6274470824bdf7b986ae418420ba9f27293d4', 'bytes':4920}
    result = {'artifact':asset} if wrapped else asset
    text = json.dumps(result, ensure_ascii=False, indent=2)
    artifact = {'name':'receipt.xlsx', 'mime':'application/octet-stream', 'content':'example'}
    output = {'asset':asset, 'artifact':artifact, 'result':result, 'text':text}
    summary = '영수증 3건을 확인했습니다.'
    run = {'id':'run', 'agent_id':'agent', 'artifact':artifact, 'outputs':{'file':output},
           'answer':summary + '\n\n' + text}
    store = Storage(tmp_path)
    try:
        store.save_test(run)
        for shown in (store.test('run'), store.tests('agent')[0]):
            assert shown['answer'] == summary
            assert shown['artifact'] == artifact and shown['outputs']['file'] == output
        raw = store.db.execute('SELECT json FROM agent_tests WHERE {ws} AND id=?', ('run',)).fetchone()[0]
        assert json.loads(raw)['answer'] == run['answer']
    finally:
        store.close()


def test_file_conversion_keeps_its_quality_report_and_pending_question():
    asset = {'path':'generated/example.pdf', 'bytes':123, 'version':'version'}
    quality = {'loss_report':{'lossy':True,'issues':['표 서식이 달라졌습니다.']}}
    result = {'artifact':asset, **quality}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    outputs = {'convert':{'asset':asset, 'artifact':{'name':'result.pdf'}, 'result':result, 'text':text}}
    answer = public_run_answer(text+'\n\n계속할까요?', outputs)
    kept, question = answer.split('\n\n')
    assert json.loads(kept) == quality
    assert question == '계속할까요?'


def test_nonfile_json_and_user_authored_text_are_still_answers():
    data = {'artifact':{'path':'generated/sample.xlsx','version':'version','bytes':100}}
    text = json.dumps(data, indent=2)
    assert public_run_answer(text, {'lookup':{'result':data,'text':text}}) == text
    message = '설명에 포함된 예시:\n' + text
    assert public_run_answer(message, {'lookup':{'result':data,'text':text,'asset':data['artifact'],
                                                 'artifact':{'name':'other.xlsx'}}}) == message
