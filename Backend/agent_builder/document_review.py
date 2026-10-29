"""Human choices over immutable parser output; no model or source-file writes."""
from copy import deepcopy
import hashlib
import json
import re
from document_analysis.contracts import project, validate

CONTRACT='a4b.document_review.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def analysis_of(asset):
    analysis=(asset.get('parser_metadata') or {}).get('document_analysis')
    if not isinstance(analysis,dict) or analysis.get('contract_version')!='a4b.document_analysis.v1':
        raise ValueError('내용 분석 결과가 있는 문서만 검토할 수 있습니다.')
    validate(analysis)
    return analysis


def groups_for(asset):
    analysis=analysis_of(asset)
    blocks=analysis['blocks'];by_id={b['id']:b for b in blocks};groups={}
    for item in blocks:
        if item.get('selected',True) or not item.get('text','').strip() or item['engine'] in {'native','openpyxl+xml','pymupdf'}:
            continue
        phase=item.get('phase')
        if phase not in {'comparison','primary'}:
            continue
        if phase=='primary':
            scope={'kind':'document'}
        elif item.get('parent_object_id'):
            scope={'kind':'image','object_id':item['parent_object_id']}
        else:
            scope={'kind':'unit','unit_id':item.get('unit_id')}
        key=digest({'scope':scope,'engine':item['engine'],'phase':phase})[:24]
        group=groups.setdefault(key,dict(id=key,scope=scope,engine=item['engine'],phase=phase,candidate_block_ids=[]))
        group['candidate_block_ids'].append(item['id'])
    def inside(item,scope):
        if scope['kind']=='document':return True
        if scope['kind']=='unit':return item.get('unit_id')==scope['unit_id']
        parent=item.get('parent_object_id')
        while parent:
            if parent==scope['object_id']:return True
            parent=by_id[parent].get('parent_object_id')
        return False
    for group in groups.values():
        group['base_block_ids']=[b['id'] for b in blocks if b.get('selected',True) and b.get('text','').strip() and inside(b,group['scope'])]
        group['replacement_block_ids']=[b['id'] for b in blocks if b.get('selected',True) and inside(b,group['scope'])]
        first=by_id[group['candidate_block_ids'][0]]
        group['native_location']=deepcopy(first['native_location'])
    return list(groups.values())


def initial_review(asset):
    analysis=analysis_of(asset)
    return dict(contract_version=CONTRACT,revision=0,status='pending',decisions={},note='',
                source_sha256=analysis['source_sha256'],analysis_sha256=digest(analysis),completed_at=None,updated_at=None)


def effective_asset(asset,review):
    if not review:return asset
    analysis=analysis_of(asset)
    if review['analysis_sha256']!=digest(analysis):raise ValueError('분석 결과가 변경되어 검토 내용을 다시 확인해야 합니다.')
    result=deepcopy(asset);effective=deepcopy(analysis);by_id={b['id']:b for b in effective['blocks']}
    for group in groups_for(asset):
        if review['decisions'].get(group['id'],{}).get('decision')!='adopt':continue
        for id in group['replacement_block_ids']:by_id[id]['selected']=False
        for id in group['candidate_block_ids']:by_id[id]['selected']=True
    projected=project(effective)
    for key in ['text','pages','sections','tables','page_count','page_kind']:result[key]=projected[key]
    result.setdefault('parser_metadata',{})['document_analysis']=effective
    result['parser_metadata']['document_review']=deepcopy(review)
    # Human completion is separate from the parser's partial/error observations.
    result['version']=int(asset.get('version') or 1)+review['revision']
    return result


def next_review(asset,current,command,now):
    if type(command.get('expected_revision')) is not int or command['expected_revision']!=current['revision']:
        raise ValueError('검토 내용이 다른 화면에서 변경됐습니다. 새로 불러온 뒤 다시 저장하세요.')
    if command.get('source_sha256')!=current['source_sha256'] or command.get('analysis_sha256')!=current['analysis_sha256']:
        raise ValueError('원본 또는 분석 결과가 변경됐습니다. 다시 불러오세요.')
    if not isinstance(command.get('request_id'),str) or not re.fullmatch(r'[0-9a-fA-F-]{36}',command['request_id']):
        raise ValueError('검토 요청 ID를 확인하세요.')
    note=command.get('note','')
    if not isinstance(note,str) or len(note)>2000:raise ValueError('검토 메모는 2,000자 이하로 입력하세요.')
    action=command.get('action');groups={g['id']:g for g in groups_for(asset)}
    updated=deepcopy(current)
    if action=='decide':
        if current['status']=='completed':raise ValueError('검토를 다시 연 뒤 결과를 변경하세요.')
        group=groups.get(command.get('group_id'));decision=command.get('decision')
        if not group or decision not in {'adopt','keep','hold'}:raise ValueError('후보와 검토 결정을 확인하세요.')
        # Reject overlapping replacements rather than silently losing a choice.
        if decision=='adopt':
            affected=set(group['replacement_block_ids'])
            for other_id,choice in current['decisions'].items():
                if other_id!=group['id'] and choice['decision']=='adopt' and affected.intersection(groups[other_id]['replacement_block_ids']):
                    raise ValueError('같은 범위의 다른 후보가 채택돼 있습니다. 먼저 기본 결과로 변경하세요.')
        updated['decisions'][group['id']]=dict(decision=decision,note=note,decided_at=now)
        updated['status']='deferred' if any(c['decision']=='hold' for c in updated['decisions'].values()) else 'in_progress'
        updated['completed_at']=None
    elif action=='complete':
        if any(updated['decisions'].get(id,{}).get('decision') not in {'adopt','keep'} for id in groups):
            raise ValueError('미결·보류 후보를 채택 또는 기본 결과 유지로 결정한 뒤 완료하세요.')
        if command.get('acknowledge') is not True:raise ValueError('원본과 파서 주의사항을 확인해야 검토를 완료할 수 있습니다.')
        updated.update(status='completed',completed_at=now,note=note,
            acknowledged_review_reasons=analysis_of(asset)['review_reasons'])
    elif action=='reopen':
        if current['status']!='completed':raise ValueError('완료된 검토만 다시 열 수 있습니다.')
        updated.update(status='in_progress',completed_at=None,note=note)
    else:raise ValueError('지원하지 않는 검토 동작입니다.')
    updated.update(revision=current['revision']+1,updated_at=now)
    effective_asset(asset,updated)  # Enforce result limits before any database write.
    return updated


def view(storage,asset_id):
    original=storage.raw_asset(asset_id);review=storage.document_review(asset_id) or initial_review(original)
    groups=groups_for(original)
    for group in groups:
        group['decision']=review['decisions'].get(group['id'],{}).get('decision','pending')
    return dict(asset=effective_asset(original,review) if review['revision'] else original,
                review=review,groups=groups,history=storage.document_review_history(asset_id))


def save(storage,asset_id,command):
    storage.db.execute('BEGIN IMMEDIATE')
    try:
        original=storage.raw_asset(asset_id);current=storage.document_review(asset_id) or initial_review(original)
        request_id=command.get('request_id','')
        prior=storage.db.execute('SELECT json FROM document_review_events WHERE asset_id=? AND request_id=?',(asset_id,request_id)).fetchone()
        if prior:
            if json.loads(prior[0])['command_sha256']!=digest(command):raise ValueError('같은 요청 ID로 다른 검토 내용을 저장할 수 없습니다.')
            storage.db.commit();return {**view(storage,asset_id),'replayed':True}
        from .storage import now
        updated=next_review(original,current,command,now())
        event=dict(revision=updated['revision'],request_id=request_id,action=command['action'],
                   group_id=command.get('group_id'),decision=command.get('decision'),note=command.get('note',''),
                   at=updated['updated_at'],command_sha256=digest(command),acknowledge=command.get('acknowledge') is True)
        storage.db.execute('INSERT INTO document_reviews VALUES(?,?,?) ON CONFLICT(asset_id) DO UPDATE SET revision=excluded.revision,json=excluded.json',
                           (asset_id,updated['revision'],json.dumps(updated,ensure_ascii=False)))
        storage.db.execute('INSERT INTO document_review_events VALUES(?,?,?,?)',(asset_id,updated['revision'],request_id,json.dumps(event,ensure_ascii=False)))
        storage.db.commit()
    except Exception:
        storage.db.rollback();raise
    return view(storage,asset_id)
