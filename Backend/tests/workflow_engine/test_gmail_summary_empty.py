from workflow_engine.executors.document.compose_answer import execute


def test_empty_gmail_collection_does_not_ask_model_to_invent_a_summary():
    mail={'id':'mail','tool':'email.list','settings':{'operation':'read_messages'}}
    summary={'id':'summary','tool':'compose_answer','settings':{},
             'bindings':[{'input':'documents','node_id':'mail','output':'documents'}]}
    state={'outputs':{'mail':{'documents':[], 'result':{'returned_count':0,'complete':True}}}}

    class UnusedModel:
        def __getattr__(self,name):
            raise AssertionError('An empty mailbox must not invoke the model')

    result=execute(summary,state,None,{'workflow':{'nodes':[mail,summary]}},UnusedModel())
    assert result=={'text':'검색 조건에 맞는 Gmail 메일이 없어 요약할 내용이 없습니다.','evidence':[]}
