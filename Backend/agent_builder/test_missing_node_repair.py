from agent_builder.nodes.supervisor import supervisor_node


def test_missing_node_can_be_repaired_but_never_approved_without_revalidation():
    state={'issues':['존재하지 않는 노드에 연결했습니다.'], 'repair_count':1,
           'verification_enforced':True,'verification_route':'manual_review',
           'status':'validation_failed','draft':{},'trace':[]}
    assert supervisor_node(state)['route']=='repair'
    state['repair_count']=2
    assert supervisor_node(state)['route']=='invalid'


def test_missing_entry_also_gets_bounded_repair():
    state={'issues':['시작 노드가 없습니다.'], 'repair_count':1,
           'verification_enforced':True,'verification_route':'manual_review',
           'status':'validation_failed','draft':{},'trace':[]}
    assert supervisor_node(state)['route']=='repair'
    state['repair_count']=2
    assert supervisor_node(state)['route']=='invalid'


def test_verifier_manual_review_is_not_bypassed_for_unrelated_issue():
    state={'issues':['자료 근거 부족'], 'repair_count':1,
           'verification_enforced':True,'verification_route':'manual_review',
           'status':'validation_failed','draft':{},'trace':[]}
    assert supervisor_node(state)['route']=='invalid'
