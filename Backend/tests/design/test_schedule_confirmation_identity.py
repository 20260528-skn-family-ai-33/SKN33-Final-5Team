"""A retry cannot silently change whether the user approved a reservation."""
import hashlib

import pytest

from infrastructure.persistence.generation_history import GenerationHistory
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage


@pytest.mark.parametrize('include',[True,False])
def test_confirmation_retry_preserves_the_selected_reservation_choice(tmp_path,include):
    store=Storage(tmp_path)
    try:
        session=store.new_session('test','local')
        data={'id':session['id'],'action':'confirm','revision':0,'generation_request_id':uid(),
              'include_schedule':include}
        audit=GenerationHistory.start(store,data)
        first=audit.read()
        assert GenerationHistory.start(store,data).read()==first
        with pytest.raises(ValueError,match='이미 처리된 생성 요청'):
            GenerationHistory.start(store,{**data,'include_schedule':not include})
        assert audit.read()==first
    finally:store.close()


@pytest.mark.parametrize('action',['message','confirm'])
def test_existing_request_hashes_without_reservation_choice_keep_their_identity(tmp_path,action):
    store=Storage(tmp_path)
    try:
        session=store.new_session('test','local')
        content='요청 원문' if action=='message' else ''
        data={'id':session['id'],'action':action,'revision':0,'content':content,'generation_request_id':uid()}
        audit=GenerationHistory.start(store,data)
        assert audit.read()['request_hash']==hashlib.sha256(content.encode()).hexdigest()
        assert GenerationHistory.start(store,data).read()==audit.read()
    finally:store.close()
