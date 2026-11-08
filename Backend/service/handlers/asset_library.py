"""Library actions: ownership comes from the server-authorized workspace directory."""
from infrastructure.persistence.asset_library import AssetLibrary

def library(c):
    if any(k in c.data for k in ('space_id','team_id','user_id','owner_id','workspace_id')):
        raise ValueError('자료 공간은 로그인 및 팀 권한을 확인한 서버에서 선택됩니다.')
    return AssetLibrary(c.storage)

def listing(c):return library(c).listing(c.data.get('folder_id'),c.data.get('before'),c.data.get('q'))

def validate_destination(c):
    current=library(c)
    return dict(folder_id=current.destination(c.data.get('folder_id')),space=current.space)

def upload(c):return dict(asset=library(c).upload(c.data['asset'],c.data['rows'],c.data.get('folder_id')))
def create_folder(c):return dict(folder=library(c).create_folder(c.data['name'],c.data.get('parent_id')))

def update_folder(c):
    if ('name' in c.data)==('parent_id' in c.data):raise ValueError('이름 변경 또는 폴더 이동 중 하나를 지정해주세요.')
    return dict(folder=library(c).update_folder(c.data['id'],name=c.data.get('name'),parent_id=c.data.get('parent_id'),move='parent_id' in c.data))

def delete_folder(c):return library(c).delete_folder(c.data['id'])
def move_asset(c):return dict(asset=library(c).move_asset(c.data['id'],c.data.get('folder_id')))
