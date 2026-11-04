"""Owner-scoped project snapshots in the existing favs key/value table.

Reserved keys have no @ and cannot collide with Google account email keys.
Updates compare revisions in the database, not in Python memory.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from app import todos

MAX_BYTES = 3_000_000

def prefix(email):
    return 'oa_sync/' + hashlib.sha256(email.strip().lower().encode()).hexdigest() + '/'

def key(email, project_id):
    return prefix(email) + str(uuid.UUID(str(project_id)))

def validate(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 1:
        raise ValueError('지원하지 않는 프로젝트 형식')
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('프로젝트 동기화 크기 제한: 3MB')
    if not isinstance(snapshot.get('name'), str) or not 1 <= len(snapshot['name']) <= 100:
        raise ValueError('프로젝트 이름 오류')
    messages = snapshot.get('messages')
    if not isinstance(messages, list) or len(messages) > 10000:
        raise ValueError('대화 개수 제한')
    for m in messages:
        if not isinstance(m, dict) or m.get('role') not in ('user', 'assistant') or not isinstance(m.get('content'), str) or len(m['content']) > 200000:
            raise ValueError('대화 형식 오류')
    if not isinstance(snapshot.get('memory'), dict) or not isinstance(snapshot.get('summary'), str):
        raise ValueError('기억 형식 오류')
    files = snapshot.get('files', [])
    if not isinstance(files, list) or len(files) > 100:
        raise ValueError('작업 파일 개수 제한: 100개')
    seen = set()
    for f in files:
        name = f.get('path', '') if isinstance(f, dict) else ''
        if not isinstance(name, str) or not name or len(name)>500 or name.startswith(('/', '\\')) or ':' in name or any(p.startswith('.') for p in re.split(r'[/\\]', name)):
            raise ValueError('작업 파일 경로 오류')
        if name.lower() in seen or not isinstance(f.get('data'), str):
            raise ValueError('작업 파일 형식 오류')
        seen.add(name.lower())
    return snapshot

def dispatch(email, op, body):
    if op == 'sync.account':
        # Fail explicitly if the existing server store is unavailable.
        todos._request('GET', 'favs', params={'select':'email','email':'like.'+prefix(email)+'*','limit':'1'})
        return {'ok':True,'email':email,'schema':1}
    if op == 'sync.list':
        rows = todos._request('GET','favs',params={'select':'email,data->id,data->revision,data->name,data->updatedAt','email':'like.'+prefix(email)+'*','limit':'501'}).json()
        if len(rows)>500: raise ValueError('프로젝트 목록 제한: 500개')
        return {'ok':True,'projects':[{'id':r['id'],'revision':r['revision'],'name':r['name'],'updatedAt':r['updatedAt']} for r in rows]}
    project_key = key(email, body.get('id'))
    if op == 'sync.get':
        rows=todos._request('GET','favs',params={'select':'data','email':'eq.'+project_key}).json()
        return {'ok':True,'project':rows[0]['data'] if rows else None}
    if op != 'sync.put': raise ValueError('지원하지 않는 동기화 요청')
    snapshot=validate(body.get('snapshot'))
    expected=body.get('revision')
    if type(expected) is not int or expected<0: raise ValueError('프로젝트 버전 오류')
    document={'id':str(uuid.UUID(str(body['id']))),'revision':expected+1,'name':snapshot['name'],'updatedAt':datetime.now(timezone.utc).isoformat(),'snapshot':snapshot}
    if expected==0:
        try:
            todos._request('POST','favs',json={'email':project_key,'data':document})
        except Exception:
            rows=todos._request('GET','favs',params={'select':'data','email':'eq.'+project_key}).json()
            if rows:return {'ok':False,'conflict':True,'message':'다른 컴퓨터에서 변경됐습니다. 원격 사본을 확인하세요.'}
            raise
    else:
        rows=todos._request('PATCH','favs',params={'email':'eq.'+project_key,'data->>revision':'eq.'+str(expected)},json={'data':document},headers={'Prefer':'return=representation'}).json()
        if not rows:return {'ok':False,'conflict':True,'message':'다른 컴퓨터에서 변경됐습니다. 원격 사본을 확인하세요.'}
    return {'ok':True,'revision':document['revision'],'updatedAt':document['updatedAt']}