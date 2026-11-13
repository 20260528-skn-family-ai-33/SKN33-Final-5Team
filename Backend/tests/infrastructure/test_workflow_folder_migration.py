"""Exercise the real importer on SQLite, including destination SQL and reruns."""
import json
import runpy
import sqlite3
from pathlib import Path
import pytest
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.workflow_folders import change

SCRIPT=Path(__file__).resolve().parents[3]/'scripts/deploy/migrate-sqlite-to-postgres.py'


def test_folder_migration_preserves_revision_assignments_and_is_idempotent(tmp_path):
    migration=runpy.run_path(str(SCRIPT))
    store=Storage(tmp_path)
    try:
        first=change(store,{'operation':'create','name':'업무 폴더','revision':0})
        state=change(store,{'operation':'rename','name':'변경한 폴더','folder_id':first['folders'][0]['id'],'revision':1})
        # A placement is copied exactly, without deriving it from agent versions.
        state['assignments']={'saved-agent':state['folders'][0]['id']}
        store.db.execute('UPDATE workflow_organization SET json=? WHERE {ws} AND id=?',
                         (json.dumps({k:v for k,v in state.items() if k!='revision'},ensure_ascii=False),'folders'))
        store.db.commit()
    finally:store.close()
    counts,orphans=migration['survey'](tmp_path)
    assert counts['workflow_organization']==1 and not orphans
    source=migration['read_only'](tmp_path/'agent-builder.sqlite')
    try:rows,skipped=migration['source_rows'](source,'workflow_organization')
    finally:source.close()
    assert skipped==0
    target=sqlite3.connect(':memory:')
    target.execute('CREATE TABLE workflow_organization(workspace_id TEXT,id TEXT,revision INTEGER,json TEXT,PRIMARY KEY(workspace_id,id))')
    class Destination:
        def execute(self,sql,values):
            return target.execute(sql.replace('a4b_builder.workflow_organization','workflow_organization').replace('%s','?').replace('::text',''),values)
    try:
        columns=migration['TABLES']['workflow_organization']
        assert migration['copy_rows'](Destination(),'member-a','workflow_organization',columns,rows)==1
        assert migration['copy_rows'](Destination(),'member-a','workflow_organization',columns,rows)==0
        revision,payload=target.execute('SELECT revision,json FROM workflow_organization WHERE workspace_id=?',('member-a',)).fetchone()
        assert {'revision':revision,**json.loads(payload)}==state
        assert migration['copy_rows'](Destination(),'member-b','workflow_organization',columns,rows)==1
        mismatched=[(rows[0][0],rows[0][1]+1,rows[0][2])]
        with pytest.raises(ValueError,match='원본과 다릅니다'):
            migration['copy_rows'](Destination(),'member-a','workflow_organization',columns,mismatched)
        assert target.execute('SELECT COUNT(*) FROM workflow_organization').fetchone()[0]==2
    finally:target.close()
