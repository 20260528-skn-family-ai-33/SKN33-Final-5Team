"""Empty joins must remain valid tables for subsequent sort and CSV export."""
import pytest

from workflow_engine.unified_tools.local_operations import join


@pytest.mark.parametrize('left_rows,right_rows', [([], []), ([{'group':'A','sum':4}], []),
    ([], [{'group':'B','count':2}]), ([{'group':'A','sum':4}], [{'group':'B','count':2}])])
def test_empty_inner_join_preserves_declared_columns(left_rows, right_rows):
    result = join({'columns':['group','sum'],'rows':left_rows},
                  {'columns':['group','count'],'rows':right_rows},
                  ['group'],'inner','one_to_one','error',100)
    assert result == {'table':{'columns':['group','sum','count'],'rows':[]}}


def test_empty_join_uses_the_same_collision_names_as_nonempty_join():
    def run(rows):
        return join({'columns':['id','value'],'rows':rows}, {'columns':['id','value'],'rows':rows},
                    ['id'],'inner','one_to_one','error',100)['table']
    assert run([])['columns'] == run([{'id':1,'value':2}])['columns'] == ['id','value','value_right']


def test_empty_join_does_not_silently_drop_colliding_columns():
    with pytest.raises(ValueError, match='collision'):
        join({'columns':['id','value','value_right'],'rows':[]}, {'columns':['id','value'],'rows':[]},
             ['id'],'inner','one_to_one','error',100)
