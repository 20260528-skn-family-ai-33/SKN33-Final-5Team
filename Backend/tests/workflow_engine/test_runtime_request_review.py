"""PR #103 review reproductions through the execution service API."""
import pytest

from service.worker import handle
from tests.workflow_engine.test_beta_feedback import table_flow
from tests.workflow_engine.test_workflow import saved


def run_rows(tmp_path, content, rows):
    store, identifier, _ = saved(tmp_path,table_flow())
    store.close()
    job = dict(directory=str(tmp_path),id=identifier)
    asset = handle({**job,'action':'run_upload',
        'asset':dict(name='orders.csv',kind='table',format='csv',columns=['상태']),
        'rows':[{'상태':value} for value in rows]})['asset']
    return handle({**job,'action':'test','content':content,'file_ids':[asset['id']]})


@pytest.mark.parametrize('content', ['완료 주문','완료 주문만','이번에는 완료 주문 추출해줘'])
def test_completed_order_aliases_apply_the_same_filter(tmp_path, content):
    result = run_rows(tmp_path,content,['완료','취소','보류'])
    assert result['status']=='succeeded',result.get('error')
    assert result['rows']==[{'상태':'완료'}]
    assert '이번 실행에 적용했습니다' in result['answer']


@pytest.mark.parametrize('content,value', [
    ('상태 = "완료;보류"','완료;보류'),
    ("상태 = '완료;보류'",'완료;보류'),
    ('상태 = "완료;보류"; 상태 != "취소"','완료;보류'),
    ('상태 = "완료\n보류"\n상태 != "취소"','완료\n보류'),
    ('상태 = "완료\\\";보류"','완료";보류'),
    ('상태 = "완료\\\\;보류"','완료\\;보류'),
    ('상태 = "완료;보류"; 상태 오름차순','완료;보류'),
    ("상태 = O'Reilly", "O'Reilly"),
])
def test_quoted_separators_remain_inside_the_compared_cell(tmp_path, content, value):
    result = run_rows(tmp_path,content,[value,'완료','취소'])
    assert result['status']=='succeeded',result.get('error')
    assert result['rows']==[{'상태':value}]


@pytest.mark.parametrize('content', ['상태 = "완료;보류',"상태 = '완료;보류\"",'상태 = "완료"junk'])
def test_malformed_quoted_requests_are_rejected_before_execution(tmp_path, content):
    with pytest.raises(ValueError,match='추가 실행 조건'):
        run_rows(tmp_path,content,['완료','취소'])
