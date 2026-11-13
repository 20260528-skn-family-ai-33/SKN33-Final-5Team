"""Workspace-scoped organization, independent of workflow versions and schedules."""
import json
from infrastructure.persistence.ids import uid

FOLDER_SCHEMA='''CREATE TABLE IF NOT EXISTS workflow_organization(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,json TEXT NOT NULL);'''


def organization(storage):
    row=storage.db.execute('SELECT revision,json FROM workflow_organization WHERE {ws} AND id=?',('folders',)).fetchone()
    return {'revision':row[0],**json.loads(row[1])} if row else {'revision':0,'folders':[],'assignments':{}}


def change(storage,data):
    storage.begin_write()
    try:
        state=organization(storage)
        if type(data.get('revision')) is not int or data['revision']!=state['revision']:
            raise ValueError('다른 곳에서 폴더가 변경됐습니다. 목록을 새로고침하고 다시 시도하세요.')
        action=data.get('operation');folder_id=data.get('folder_id')
        folder=next((f for f in state['folders'] if f['id']==folder_id),None)
        if action in ('rename','delete') or action=='move' and folder_id is not None:
            if not folder:raise ValueError('폴더를 찾을 수 없습니다. 목록을 새로고침하세요.')
        if action in ('create','rename'):
            name=data.get('name')
            if not isinstance(name,str) or not 1<=len(name.strip())<=60 or any(ord(c)<32 or c in '\x7f\x85\u2028\u2029' for c in name):
                raise ValueError('폴더 이름을 한 줄로 1~60자 입력하세요.')
            name=name.strip()
            if any(f['name'].casefold()==name.casefold() and (action=='create' or f['id']!=folder_id) for f in state['folders']):
                raise ValueError('같은 이름의 폴더가 있습니다.')
            if action=='create':
                if len(state['folders'])>=200:raise ValueError('폴더는 최대 200개까지 만들 수 있습니다.')
                state['folders'].append({'id':uid(),'name':name})
            else:folder['name']=name
        elif action=='delete':
            state['folders']=[f for f in state['folders'] if f['id']!=folder_id]
            state['assignments']={a:f for a,f in state['assignments'].items() if f!=folder_id}
        elif action=='move':
            ids=data.get('agent_ids')
            if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(not isinstance(a,str) for a in ids):
                raise ValueError('이동할 워크플로우를 선택하세요. 한 번에 최대 500개까지 이동할 수 있습니다.')
            for agent_id in ids:storage.agent(agent_id)
            for agent_id in ids:
                if folder_id is None:state['assignments'].pop(agent_id,None)
                else:state['assignments'][agent_id]=folder_id
        else:raise ValueError('폴더 작업을 확인하세요.')
        revision=state.pop('revision')+1
        storage.db.execute('INSERT INTO workflow_organization({wsc}id,revision,json) VALUES({wsv}?,?,?) ON CONFLICT({wsc}id) DO UPDATE SET revision=excluded.revision,json=excluded.json',('folders',revision,json.dumps(state,ensure_ascii=False)))
        storage.db.commit()
        return {'revision':revision,**state}
    except Exception:
        storage.db.rollback()
        raise
