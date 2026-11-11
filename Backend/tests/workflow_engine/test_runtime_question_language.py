from workflow_engine.executors.control import ask_user
from workflow_engine.executors.schemas import InputAssessment


class Model:
    def __init__(self,values):self.values=list(values);self.calls=[]
    def with_structured_output(self,*args,**kwargs):return self
    def invoke(self,messages):
        self.calls.append(messages)
        return self.values.pop(0)


def node(question='계속 진행할까요?',instructions=None):
    return {'id':'ask','tool':'ask_user','bindings':[], 'settings':{'question':question,'instructions':instructions}}


def test_initial_question_is_rewritten_once_before_becoming_visible(monkeypatch):
    model=Model([InputAssessment(valid=False,clarification='어떤 형식으로 정리할까요?')])
    seen=[]
    monkeypatch.setattr(ask_user,'interrupt',lambda payload:seen.append(payload['question']) or '표 형식')
    result=ask_user.execute(node('结果要怎样整理？'),{'outputs':{},'messages':[]},None,None,model)
    assert seen==['어떤 형식으로 정리할까요?']
    assert result['answer']=='표 형식'
    assert len(model.calls)==1


def test_model_followup_uses_korean_repair_without_changing_answer_judgment(monkeypatch):
    model=Model([InputAssessment(valid=False,clarification='请选择处理方式'),
                 InputAssessment(valid=False,clarification='어떤 처리 방식을 원하시나요?'),
                 InputAssessment(valid=True,clarification='')])
    answers=iter(['추가 확인','진행해 주세요']);seen=[]
    monkeypatch.setattr(ask_user,'interrupt',lambda payload:seen.append(payload['question']) or next(answers))
    result=ask_user.execute(node(instructions='진행 여부를 확인하세요.'),{'outputs':{},'messages':[]},None,None,model)
    assert seen==['계속 진행할까요?','어떤 처리 방식을 원하시나요?']
    assert result['answer']=='진행해 주세요'
    assert len(model.calls)==3


def test_failed_repair_falls_back_to_the_original_korean_question(monkeypatch):
    model=Model([InputAssessment(valid=False,clarification='请确认'),
                 InputAssessment(valid=False,clarification='请选择'),
                 InputAssessment(valid=True,clarification='')])
    seen=[];answers=iter(['미정','계속 진행'])
    monkeypatch.setattr(ask_user,'interrupt',lambda payload:seen.append(payload['question']) or next(answers))
    result=ask_user.execute(node(instructions='진행 여부'),{'outputs':{},'messages':[]},None,None,model)
    assert seen==['계속 진행할까요?','계속 진행할까요?']
    assert result['answer']=='계속 진행'


def test_user_provided_names_are_preserved_without_a_model_call(monkeypatch):
    model=Model([]);seen=[]
    monkeypatch.setattr(ask_user,'interrupt',lambda payload:seen.append(payload['question']) or '네')
    ask_user.execute(node('「年月」 열을 사용할까요?'),{'outputs':{},'messages':[{'role':'user','content':'年月 열을 사용해줘.'}]},None,None,model)
    assert seen==['「年月」 열을 사용할까요?']
    assert not model.calls
