import json

from workflow_engine.public_runs import public_run,public_result


def test_500_long_korean_messages_fit_the_worker_limit_without_changing_persisted_outputs():
    documents=[{'source_id':f'gmail:{index}','parser_engine':'gmail','file_name':f'메일{index}',
                'text':'한글메일본문'*1333,'page':None} for index in range(500)]
    evidence=[{**document,'quote':document['text']} for document in documents]
    answer='모든500건의 요약 결과입니다.\n'+'요약내용'*50000
    original={'id':'run','agent_id':'workflow','status':'succeeded','config_snapshot':{},'answer':answer,
              'outputs':{'mail':{'documents':documents,'result':{'documents':documents},'text':'\n'.join(d['text'] for d in documents)},
                         'summary':{'text':answer,'evidence':evidence}},
              'evidence':evidence,'messages':[{'role':'user','content':'전날 메일을 요약하세요.'},{'role':'assistant','content':answer}],
              'steps':[{'node':'mail','status':'completed'}],'rows':[]}
    projected=public_run(original)
    assert len(json.dumps(projected,ensure_ascii=False).encode())<12*1024*1024
    assert projected['answer']==answer and projected['steps']==original['steps']
    assert len(projected['evidence'])==500 and all(len(item['quote'])<=1000 for item in projected['evidence'])
    assert all('text' not in item for item in projected['evidence']) and 'outputs' not in projected
    assert original['outputs']['mail']['documents'] is documents
    assert original['evidence'][0]['text']==documents[0]['text']
    assert len(original['messages'])==2 and len(original['evidence'][0]['quote'])>1000
    assert public_result({'run':original})['run']==projected
    assert public_result({'tests':[original]})['tests']==[projected]
    history=public_result({'tests':[original]*20})
    assert len(json.dumps(history,ensure_ascii=False).encode())<12*1024*1024
    assert history['tests'][0]['answer']==answer
    assert history['tests'][1]['public_projection']['history_preview']
    assert history['tests'][1]['evidence']==[]


def test_small_run_responses_preserve_existing_output_contracts():
    original={'id':'run','config_snapshot':{},'outputs':{'mail':{'documents':[{'source_id':'gmail:one','text':'본문'}]}},'answer':'요약','evidence':[]}
    assert public_run(original) is original
    assert public_result(original) is original
