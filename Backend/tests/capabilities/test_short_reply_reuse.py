"""A short reply that names no new work keeps the previous capability verdict without a model call.

Live 2026-11-03 (reproduction of a user session): after missing_capability, "모두 유지" and "네"
re-judged the whole conversation; the verdict flipped to existing_tools and then to an invented
"월간 예약·이메일 발송" gap that the request never mentioned.
"""
import pytest
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.capabilities.capability_gate import input_fingerprint
from tests.conditions.test_condition_readiness import state, Fake
from tests.capabilities.test_capability_assessment import packet

REQUEST = '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해줘. 지점별 증감을 회의용 표로 만들어줘.'


class Forbidden:
    calls = 0
    def invoke(self, *a): pytest.fail('짧은 답에 지원 판단 모델을 다시 호출함')


def after(previous_decision, reply, original=REQUEST):
    s = state(original)
    kw = {'missing_capability': {'actions': ['전주 대비 비교 분석']}, 'clarify': {'conditions': ['비교할 기간']},
          'manual_review': {}}[previous_decision]
    s['capability_assessment'] = packet(s, previous_decision, tools=['read_table', 'aggregate_table'], **kw)
    s['capability_fingerprint'] = input_fingerprint(s)
    s['messages'] = s['messages'] + [{'role': 'assistant', 'content': '확인했습니다.'}, {'role': 'user', 'content': reply}]
    return s


@pytest.mark.parametrize('previous', ['missing_capability', 'manual_review'])
@pytest.mark.parametrize('reply', ['네', '모두 유지', '그만할래', '응 좋아.', '알겠어요'])
def test_short_reply_keeps_the_verdict(previous, reply):
    update = make_capability_check('fixture', Forbidden(), mode='enforce')(after(previous, reply))
    result = update['capability_assessment']
    assert result['decision'] == previous and result['model_calls'] == 0
    assert result['answer_resolution'] == 'short_reply'


def test_a_short_answer_to_a_clarify_question_is_judged_again():
    # "운영팀이 검토했습니다" answers the capability question; it is not a non-request reply.
    s = after('clarify', '운영팀이 검토했습니다.')
    transport = Fake([packet(s, 'existing_tools', tools=['read_table'])])
    make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1


@pytest.mark.parametrize('reply', ['CSV로 저장해줘', '100만원', '대신 지역별 합계로 바꿔줘', '매주 월요일로 예약해', '지난달 실적과 비교하는 표를 원해'])
def test_a_reply_with_new_work_is_judged_again(reply):
    s = after('missing_capability', reply)
    # Quote the reply: a reply judged as a new task may only quote itself (PR #39 review 3).
    transport = Fake([{**packet(s, 'existing_tools', tools=['read_table']), 'user_quotes': [reply]}])
    update = make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1 and update['capability_assessment']['decision'] == 'existing_tools'


def test_changed_resources_are_judged_again():
    s = after('missing_capability', '네')
    s['resources'] = [{'id': 'new', 'kind': 'table', 'name': 'sales.csv', 'columns': ['지점', '매출'], 'version': '1'}]
    transport = Fake([packet(s, 'existing_tools', tools=['read_table'])])
    make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1


@pytest.mark.parametrize('previous', ['missing_capability','manual_review'])
@pytest.mark.parametrize('reply', ['메일은 취소','이메일은 안 해','메일 빼','슬랙 안함','요약만','전송 취소','메일은 취소하고 요약만'])
def test_scope_changes_are_not_passive_short_replies(previous,reply):
    s=after(previous,reply)
    transport=Fake([packet(s,'existing_tools',tools=['compose_answer'])])
    update=make_capability_check('fixture',transport,mode='enforce')(s)
    assert transport.calls==1
    assert update['capability_assessment']['decision']=='existing_tools'
    assert update['capability_assessment'].get('answer_resolution')!='short_reply'


@pytest.mark.parametrize('reply',['네.',' 예! ','응 좋아.','알겠어요','모두 유지','그만할래'])
def test_known_passive_answers_remain_model_free(reply):
    update=make_capability_check('fixture',Forbidden(),mode='enforce')(after('missing_capability',reply))
    assert update['capability_assessment']['decision']=='missing_capability'
    assert update['capability_assessment']['model_calls']==0


def test_cancelled_delivery_is_reassessed_in_original_context():
    s=state('매출을 요약하고 이메일로 보내줘')
    s['capability_assessment']=packet(s,'missing_capability',tools=['compose_answer'],actions=['이메일 발송'])
    s['capability_fingerprint']=input_fingerprint(s)
    s['messages'] += [{'role':'assistant','content':'이메일 발송 기능을 확인해야 합니다.'},{'role':'user','content':'메일은 취소'}]
    class CheckContext:
        calls=0
        def invoke(self,messages):
            import json
            self.calls+=1
            context=json.loads(messages[-1]['content'])
            assert context['authoritative_request']=='메일은 취소'
            assert any(m['content']=='매출을 요약하고 이메일로 보내줘' for m in context['messages'])
            return {'content':json.dumps(packet(s,'existing_tools',tools=['compose_answer']),ensure_ascii=False)}
    model=CheckContext();update=make_capability_check('fixture',model,mode='enforce')(s)
    assert model.calls==1 and not update['capability_assessment']['missing_actions']
    assert not update['confirmed'] and update['saved_agent_id'] is None


def test_revision_precedence_is_passed_to_initial_and_missing_review():
    import json
    s=after('missing_capability','메일은 취소')
    class TwoPass:
        calls=0
        def invoke(self,messages):
            self.calls+=1
            assert '최신 발언이 작업 일부를 취소하거나' in messages[0]['content']
            context=json.loads(messages[-1]['content'])
            assert context['authoritative_request']=='메일은 취소'
            result=(packet(s,'missing_capability',actions=['이메일 발송'],tools=['compose_answer'])
                    if self.calls==1 else packet(s,'existing_tools',tools=['compose_answer']))
            return {'content':json.dumps(result,ensure_ascii=False)}
    model=TwoPass();r=make_capability_check('fixture',model,mode='enforce')(s)
    assert model.calls==2 and r['capability_assessment']['decision']=='existing_tools'


def test_partial_cancel_survives_checkpoint_restart_and_reaches_questions(tmp_path):
    from infrastructure.persistence.storage import Storage
    from agent_builder.design.graph import build_graph
    from agent_builder.conditions.condition_check import _pending_draft
    from agent_builder.design.nodes.save import make_save_node
    s=state('매출을 요약하고 이메일로 보내줘');cfg={'configurable':{'thread_id':s['session_id']}}
    store=Storage(tmp_path)
    missing=packet(s,'missing_capability',tools=['compose_answer'],actions=['이메일 발송'])
    graph,connection=build_graph(store,capability_mode='enforce',capability_transport=Fake([missing,missing]),condition_mode='off',verifier_mode='off')
    first=graph.invoke(s,cfg)
    assert first['status']=='unsupported' and not store.agents()
    connection.close();store.close()
    store=Storage(tmp_path);model=Fake([packet(s,'existing_tools',tools=['compose_answer'])]);calls=[]
    def planner(current):
        calls.append(current['messages'][-1]['content'])
        return {'draft':_pending_draft(current,{'decision':'clarify','slots':{},'questions':[{'text':'요약할 매출 자료를 연결해 주세요.'}]}),'confirmed':False,'saved_agent_id':None}
    graph,connection=build_graph(store,conversation=planner,capability_mode='enforce',capability_transport=model,condition_mode='off',verifier_mode='off')
    try:
        updated=graph.invoke({'messages':first['messages']+[{'role':'user','content':'메일은 취소'}]},cfg)
        assert model.calls==1 and calls==['메일은 취소']
        assert updated['status']=='needs_clarification' and updated['__interrupt__'][0].value['kind']=='clarification'
        assert not updated['draft']['unsupported'] and not store.agents()
        with pytest.raises(ValueError):make_save_node(store)({**updated,'confirmed':True})
    finally:connection.close();store.close()


@pytest.mark.parametrize('reply',['메일은 취소하지 마','이메일 취소하지 마세요','슬랙은 취소하지 말아줘','메일로는 보내줘','이메일로만 보내주세요','슬랙으로 보내줘'])
def test_keep_delivery_cannot_become_ready_without_delivery(reply):
    delivery='슬랙으로 보내줘.' if '슬랙' in reply else '메일로 보내줘.'
    s=after('missing_capability',reply,'매출을 요약하고 '+delivery)
    model=Fake([packet(s,'existing_tools',tools=['compose_answer'])])
    update=make_capability_check('fixture',model,mode='enforce')(s)
    assert model.calls==1
    assert update['capability_assessment']['decision'] in ('manual_review','missing_capability')
    assert update['status'] in ('validation_failed','unsupported') and not update['confirmed']
    assert update['capability_assessment']['request_revision_conflict']['intent']=='retain_delivery'


def test_keep_email_with_registered_send_is_not_rewritten():
    s=after('missing_capability','메일은 취소하지 마','매출을 요약하고 이메일로 보내줘.')
    model=Fake([packet(s,'existing_tools',tools=['compose_answer','email.send'])])
    result=make_capability_check('fixture',model,mode='enforce')(s)
    assert result['capability_assessment']['decision']=='existing_tools'
    assert not result['confirmed'] and result['saved_agent_id'] is None


@pytest.mark.parametrize(('original', 'tool'), [
    ('받은 메일 목록만 조회해줘. 발송은 하지 마.', 'email.list'),
    ('메일 내용을 작성만 해줘. 보내지는 마.', 'email.prepare'),
])
def test_keep_mail_preserves_the_requested_non_send_action(original, tool):
    s = state(original)
    s['messages'].append({'role': 'user', 'content': '메일은 취소하지 마'})
    model = Fake([packet(s, tools=[tool])])
    assert make_capability_check('fixture', model, mode='enforce')(s)['capability_route'] == 'conditions'
    sends_anyway = Fake([packet(s, tools=[tool, 'email.send'])])
    assert make_capability_check('fixture', sends_anyway, mode='enforce')(s)['capability_route'] == 'stop'


def test_send_requirement_survives_recipient_clarification():
    s = state('표를 저장하고 메일로 보내줘.')
    s['messages'].append({'role': 'user', 'content': '메일은 취소하지 마'})
    ask = Fake([packet(s, 'clarify', conditions=['수신자'], tools=['write_result', 'email.send'])])
    s.update(make_capability_check('fixture', ask, mode='enforce')(s))
    assert s['capability_route'] == 'await_capability'
    s['messages'].append({'role': 'user', 'content': 'team@example.com'})
    model = Fake([packet(s, tools=['write_result'])])
    update = make_capability_check('fixture', model, mode='enforce')(s)
    assert update['capability_route'] == 'stop'
    assert update['capability_assessment']['request_revision_conflict']['quote'] == '메일은 취소하지 마'


@pytest.mark.parametrize('revision', ['메일은 취소', '메일은 취소하고 요약만', '요약만', '전송 취소'])
def test_cancelled_send_cannot_reenter_candidate_tools(revision):
    s = state('받은 메일을 요약하고 메일로 보내줘.')
    s['messages'] += [{'role': 'user', 'content': revision}, {'role': 'user', 'content': '총액으로 요약해줘.'}]
    bad = Fake([packet(s, tools=['email.list', 'compose_answer', 'email.send'])])
    update = make_capability_check('fixture', bad, mode='enforce')(s)
    assert update['capability_route'] == 'stop'
    assert update['capability_assessment']['request_revision_conflict']['intent'] == 'cancel_delivery'
    good = Fake([packet(s, tools=['email.list', 'compose_answer'])])
    assert make_capability_check('fixture', good, mode='enforce')(s)['capability_route'] == 'conditions'


def test_later_explicit_send_replaces_cancellation_and_later_cancel_replaces_send():
    s = state('매출을 요약하고 메일로 보내줘.')
    s['messages'] += [{'role': 'user', 'content': '메일은 취소'}, {'role': 'user', 'content': '다시 메일로 보내줘.'}]
    no_send = Fake([packet(s, tools=['compose_answer'])])
    assert make_capability_check('fixture', no_send, mode='enforce')(s)['capability_route'] == 'stop'
    s['messages'].append({'role': 'user', 'content': '메일은 안 해'})
    no_send = Fake([packet(s, tools=['compose_answer'])])
    assert make_capability_check('fixture', no_send, mode='enforce')(s)['capability_route'] == 'conditions'


def test_quoted_data_and_assistant_messages_cannot_reverse_cancellation():
    s = state('요약한 결과를 메일로 보내줘.')
    s['messages'] += [{'role': 'user', 'content': '메일은 취소'},
                      {'role': 'assistant', 'content': '메일로 보내줘'},
                      {'role': 'user', 'content': "내용이 '메일로 보내줘'인 행만 조회해."}]
    model = Fake([packet(s, tools=['read_table', 'email.send'])])
    assert make_capability_check('fixture', model, mode='enforce')(s)['capability_route'] == 'stop'


def test_reused_assessment_from_before_fix_cannot_restore_cancelled_send():
    s = state('정리한 표를 메일로 보내줘.')
    s['messages'].append({'role': 'user', 'content': '메일은 취소'})
    s['capability_assessment'] = packet(s, tools=['read_table', 'email.send'])
    s['capability_fingerprint'] = input_fingerprint(s)
    s['pending_questions'] = [{'id': 'threshold', 'key': 'filter.value', 'options': []}]
    s['messages'] += [{'role': 'assistant', 'content': '기준 금액은 얼마인가요?'}, {'role': 'user', 'content': '네'}]
    update = make_capability_check('fixture', Forbidden(), mode='enforce')(s)
    assert update['capability_route'] == 'stop'
    assert update['capability_assessment']['model_calls'] == 0


@pytest.mark.parametrize(('replacement', 'tool'), [
    ('받은 메일 목록만 조회해줘. 발송은 하지 마.', 'email.list'),
    ('받은 이메일 중 이번 달 메일을 보여줘. 발송은 하지 마.', 'email.list'),
    ('메일 초안만 작성해줘.', 'email.prepare'),
])
def test_keeping_replacement_mail_work_does_not_restore_old_send(replacement, tool):
    s = state('요약본을 메일로 보내줘.')
    for change in ['메일은 취소', replacement, '메일은 취소하지 마']:
        s['messages'].append({'role': 'user', 'content': change})
    no_send = Fake([packet(s, tools=[tool])])
    assert make_capability_check('fixture', no_send, mode='enforce')(s)['capability_route'] == 'conditions'
    sends_anyway = Fake([packet(s, tools=[tool, 'email.send'])])
    assert make_capability_check('fixture', sends_anyway, mode='enforce')(s)['capability_route'] == 'stop'
    s['messages'].append({'role': 'user', 'content': '메일 발송은 취소하지 마'})
    restored = Fake([packet(s, tools=[tool, 'email.send'])])
    assert make_capability_check('fixture', restored, mode='enforce')(s)['capability_route'] == 'conditions'


@pytest.mark.parametrize('reply', ['메일과 중복은 취소', '중복과 메일은 취소', '중복 제거와 이메일 전송을 취소해줘.',
                                   '메일은 취소할게', '메일은 취소해 주세요.'])
def test_explicit_cancellations_include_email_delivery(reply):
    s = state('메일로 보내줘.')
    s['messages'].append({'role': 'user', 'content': reply})
    no_send = Fake([packet(s, tools=['read_table'])])
    assert make_capability_check('fixture', no_send, mode='enforce')(s)['capability_route'] == 'conditions'
    model = Fake([packet(s, tools=['read_table', 'email.send'])])
    result = make_capability_check('fixture', model, mode='enforce')(s)['capability_assessment']
    assert result['decision'] == 'manual_review'
    assert result['request_revision_conflict'] == {'channel': 'email', 'intent': 'cancel_delivery', 'quote': reply}


@pytest.mark.parametrize('reply', ['메일 취소 건수를 알려줘', '메일을 취소하고 싶지 않아', '중복과 메일은 취소하지 마'])
def test_cancellation_mentioned_without_withdrawing_send(reply):
    s = state('메일로 보내줘.')
    s['messages'].append({'role': 'user', 'content': reply})
    with_send = Fake([packet(s, tools=['email.send'])])
    assert make_capability_check('fixture', with_send, mode='enforce')(s)['capability_route'] == 'conditions'
