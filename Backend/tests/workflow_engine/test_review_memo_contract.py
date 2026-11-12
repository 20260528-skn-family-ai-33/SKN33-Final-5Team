import json
from copy import deepcopy

import pytest

from workflow_engine.executors.document.review_memo import compose_review_memo
from tests.design.test_feedback_service_scenarios import CONTRACT_REQUEST


OPINION='월 이용료를 90000원으로 협의하자는 의견이며 아직 계약을 수정하지 않았습니다.'
EVIDENCE={'e1':{'quote':'월 이용료는 100000원입니다.','source_id':'contract:1'}}
GOOD={'sections':{'요약':'계약서의 월 이용료는 100000원이다.',
                  '주요 발견':'이용료 변경은 합의되지 않았다.',
                  '검토 의견':OPINION,'후속 조치':'담당자 제안의 협의 여부를 확인한다.'},'citations':['e1']}


class Model:
    def __init__(self,responses):self.responses=iter(responses);self.messages=[]
    def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
    def invoke(self,messages):self.messages.append(messages);return deepcopy(next(self.responses))


def run(model,**changes):
    inputs=dict(text='이전 계약 분석',answer=OPINION);inputs.update(changes)
    return compose_review_memo({'instructions':CONTRACT_REQUEST},{'instructions':CONTRACT_REQUEST},inputs,EVIDENCE,model)


def test_memo_preserves_human_proposal_separately_from_document_fact():
    model=Model([GOOD,GOOD]);result=run(model)
    assert result['text'].count('## ')==4
    assert '## 검토 의견\n'+OPINION in result['text']
    assert '100000원' in result['text'] and '90000원' in result['text']
    assert result['evidence']==[EVIDENCE['e1']]
    assert len(model.messages)==2
    context=json.loads(model.messages[0][-1]['content'])
    assert context['human_review']==OPINION and context['document_evidence'][0]['quote']==EVIDENCE['e1']['quote']


@pytest.mark.parametrize('phase',[0,1])
@pytest.mark.parametrize('change',['opinion','missing_section','empty_section','unknown_citation','no_citation','repeated_analysis'])
def test_model_cannot_discard_review_contract_even_during_fact_check(phase,change):
    invalid=deepcopy(GOOD)
    if change=='opinion':invalid['sections']['검토 의견']='90000원으로 변경 완료'
    elif change=='missing_section':del invalid['sections']['후속 조치']
    elif change=='empty_section':invalid['sections']['요약']=' '
    elif change=='unknown_citation':invalid['citations']=['invented']
    elif change=='no_citation':invalid['citations']=[]
    else:invalid={'text':'이전 계약 분석','citations':[]}
    with pytest.raises(ValueError,match='응답 계약'):
        run(Model([invalid] if phase==0 else [GOOD,invalid]))


@pytest.mark.parametrize('changes',[{'answer':''},{'answer':None},{'text':'','records':[]}])
def test_review_memo_does_not_run_without_real_bound_analysis_and_answer(changes):
    model=Model([])
    assert run(model,**changes) is None
    assert model.messages==[]
