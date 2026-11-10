"""The authenticated server refreshes metadata on initial and resumed design turns."""
import pytest

from infrastructure.persistence.ids import uid
from service.worker import handle
from tests.design.test_builder import PROMPT, fake_conversation


@pytest.fixture(autouse=True)
def local_modes(monkeypatch):
    for name in ('CAPABILITY','CONDITION','REQUIREMENT','VERIFIER'):
        monkeypatch.setenv('A4B_'+name+'_MODE','off')


@pytest.mark.parametrize('following',[False,None,'untrusted-true'])
def test_worker_refreshes_connection_context_when_resuming_a_question(tmp_path,following):
    seen=[]
    def planner(state):
        seen.append(state.get('gmail_connected'))
        assert 'gmail_access_token' not in state
        return fake_conversation(state)
    common={'directory':str(tmp_path)}
    session=handle({**common,'action':'new_session','model':'fixture','credential_id':uid()})
    first=handle({**common,'action':'message','id':session['id'],'revision':0,
                  'content':PROMPT,'gmail_connected':True},conversation=planner)
    assert first['status']=='needs_clarification'
    second=handle({**common,'action':'message','id':session['id'],'revision':first['revision'],
                   'content':'중복은 유지','gmail_connected':following},conversation=planner)
    assert second['status']=='ready'
    assert seen==[True,following if type(following) is bool else None]
    saved=handle({**common,'action':'confirm','id':session['id'],'revision':second['revision'],
                  'gmail_connected':False},conversation=planner)
    assert saved['status']=='saved'
