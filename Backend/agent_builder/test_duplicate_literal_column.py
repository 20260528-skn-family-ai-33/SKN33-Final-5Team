from agent_builder.condition_readiness import explicit_policies, source_catalog


def check(text, columns):
    state={'messages':[{'role':'user','content':text}], 'resources':[{'id':'t','kind':'table','columns':columns}]}
    catalog,_=source_catalog(state)
    return explicit_policies(state,catalog).get('duplicate.field')


def test_recognises_explicit_attached_column():
    item=check('중복 거래ID는 마지막 행만 남겨줘', ['거래ID','비용'])
    assert item['value']=='거래ID' and item['status']=='specified'


def test_no_unattached_or_ambiguous_or_negated_guess():
    assert check('중복 거래ID는 마지막 행만 남겨줘', ['다른ID']) is None
    assert check('중복 거래ID는 말고 다른ID를 써줘', ['거래ID','다른ID']) is None
    assert check('중복 거래ID와 비용을 확인해줘', ['거래ID','비용']) is None
