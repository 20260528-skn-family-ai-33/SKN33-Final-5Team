import json
import pytest
from agent_builder.graph import _timed_node


def test_stage_events_record_success_without_user_content(tmp_path, monkeypatch):
    path=tmp_path/'events.jsonl'
    monkeypatch.setenv('A4B_TIMING_LOG',str(path))
    out=_timed_node('example',lambda state:{'value':1})( {'trace':[],'secret':'private'} )
    events=[json.loads(line) for line in path.read_text().splitlines()]
    assert [e['event'] for e in events]==['start','end']
    assert events[-1]['elapsed_ms']>=0 and out['value']==1
    assert 'private' not in path.read_text()


def test_stage_events_record_exception_and_preserve_it(tmp_path, monkeypatch):
    path=tmp_path/'events.jsonl'
    monkeypatch.setenv('A4B_TIMING_LOG',str(path))
    def fail(state): raise ValueError('secret details')
    with pytest.raises(ValueError): _timed_node('example',fail)({})
    events=[json.loads(line) for line in path.read_text().splitlines()]
    assert [e['event'] for e in events]==['start','error']
    assert events[-1]['error_type']=='ValueError' and 'secret' not in path.read_text()
