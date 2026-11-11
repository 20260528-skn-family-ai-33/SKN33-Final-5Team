"""Trusted Node bridge actions for scoped library input resolution."""
from workflow_engine.scheduling.library_sources import LibrarySources,library_export


def source(c):
    manager=LibrarySources(c.storage);data=c.data
    if 'selection' in data:return {'selection':manager.save(data['id'],data['selection'],data.get('base_revision',0))}
    c.storage.agent(data['id'])
    return {'selection':manager.get(data['id'])}


def plan(c):return LibrarySources(c.storage).plan(c.data['source_action'],c.data['id'])


def original_plan(c):return LibrarySources(c.storage).original_plan(c.data['run_id'],c.data['asset_id'])


def export(c):return library_export(c.storage,c.data['selection'],c.data['target_kind'],c.data.get('refs'),c.data.get('verify_only',False))


def resolve(c):
    data=c.data
    if 'selection' not in data:raise ValueError('확인한 자료함 설정을 함께 전달하세요.')
    return LibrarySources(c.storage).resolve(data['source_action'],data['id'],data.get('candidates',[]),data.get('lease_token'),data.get('run_id'),data['selection'],use_schedule=data.get('use_schedule',False))
