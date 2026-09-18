"""Owner-only desktop gateway to existing One AI Gen records and Google account."""
from __future__ import annotations
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from app import gmail, todos, store, desktop_auth
KST=ZoneInfo('Asia/Seoul')
SOURCES={
 'meetings':('meetings','id,title,transcript,minutes,created_at','/meeting'),
 'mail':('mail_items',gmail.ITEM_FIELDS,'/mail'),
}
def account():
 rows=todos._request('GET','google_tokens',params={'select':'email','limit':'2'}).json()
 if len(rows)!=1: raise ValueError('사이트에서 Google 메일 계정을 하나 연결해주세요.')
 return rows[0]['email']

def records(email,source,offset=0):
 if source not in SOURCES:raise ValueError('지원하지 않는 기록 종류입니다.')
 table,fields,url=SOURCES[source]
 owner=account() if source=='mail' else email
 offset=int(offset)
 if offset<0:raise ValueError('잘못된 페이지입니다.')
 page_size=200 if source=='mail' else 25
 rows=todos._request('GET',table,params={'select':fields,'email':f'eq.{owner}','order':'id.asc','offset':str(offset),'limit':str(page_size)}).json()
 return {'items':[{'source':source,'id':str(r['id']),'title':r.get('title') or r.get('subject') or r.get('name') or r.get('label') or '기록','text':json.dumps(r,ensure_ascii=False),'url':'https://www.oneaigen.com'+url} for r in rows], 'next':offset+len(rows) if len(rows)==page_size else None}

def event_body(schedule):
 title=str(schedule.get('title') or '').strip()
 if not title or len(title)>200:raise ValueError('일정 제목을 1~200자로 알려주세요.')
 day=date.fromisoformat(str(schedule.get('date','')))
 clock=str(schedule.get('time') or '')
 body={'summary':title,'description':str(schedule.get('description') or '자비스에서 등록')[:1000]}
 if schedule.get('location'):body['location']=str(schedule['location'])[:300]
 if clock:
  if not re.fullmatch(r'\d{2}:\d{2}',clock):raise ValueError('시간은 HH:MM 형식이어야 합니다.')
  start=datetime.fromisoformat(f'{day}T{clock}:00').replace(tzinfo=KST)
  duration=int(schedule.get('duration_min') or 60)
  if not 1<=duration<=1440:raise ValueError('일정 길이는 1분~24시간이어야 합니다.')
  body.update(start={'dateTime':start.isoformat(),'timeZone':'Asia/Seoul'},end={'dateTime':(start+timedelta(minutes=duration)).isoformat(),'timeZone':'Asia/Seoul'})
 else:body.update(start={'date':day.isoformat()},end={'date':(day+timedelta(days=1)).isoformat()})
 return body

def event_list(acct,start,end):
 start=datetime.fromisoformat(start.replace('Z','+00:00'))
 end=datetime.fromisoformat(end.replace('Z','+00:00'))
 if start.tzinfo is None or end.tzinfo is None or end<=start or (end-start).days>366:raise ValueError('조회 기간은 시간대가 있는 1년 이내로 지정해주세요.')
 params={'timeMin':start.isoformat(),'timeMax':end.isoformat(),'singleEvents':'true','orderBy':'startTime','maxResults':250}
 items=[]
 while True:
  data=gmail._api(acct,'GET',gmail.CAL+'/calendars/primary/events',params=params)
  items.extend({k:x.get(k) for k in ('id','summary','start','end','location','description','htmlLink','etag','status','transparency','attendees')} for x in data.get('items',[]))
  if not data.get('nextPageToken'):return items
  params['pageToken']=data['nextPageToken']

def boundary(part):
 return part.get('dateTime') or part['date']+'T00:00:00+09:00'
def overlaps(acct,body,exclude=''):
 return [x for x in event_list(acct,boundary(body['start']),boundary(body['end'])) if x['id']!=exclude and x.get('status')!='cancelled' and x.get('transparency')!='transparent' and not any(a.get('self') and a.get('responseStatus')=='declined' for a in (x.get('attendees') or []))]

def preview(email,payload):
 acct=account();body=event_body(payload.get('schedule') or {})
 event_id=str(payload.get('event_id') or '')
 fields={'email':email,'account':acct,'body':body,'id':event_id,'nonce':secrets.token_hex(16)}
 if event_id:
  if not re.fullmatch(r'[a-zA-Z0-9_-]{1,1024}',event_id):raise ValueError('잘못된 일정 ID')
  prior=gmail._api(acct,'GET',gmail.CAL+'/calendars/primary/events/'+event_id)
  if prior.get('attendees'):raise ValueError('참석자가 있는 일정 수정은 Google 캘린더에서 해주세요.')
  fields['etag']=prior.get('etag')
 conflicts=overlaps(acct,body,event_id)
 return {'ok':True,'preview':body,'conflicts':conflicts,'token':desktop_auth.seal('calendar-action',fields,600)}

def commit(email,token):
 fields=desktop_auth.read(token,'calendar-action')
 if fields['email']!=email or fields['account']!=account():raise ValueError('다시 일정을 확인해주세요.')
 acct=fields['account'];body=fields['body'];eid=fields['id']
 if not eid:
  eid='jarvis'+hashlib.sha256(token.encode()).hexdigest()[:40]
  # Idempotent retries: deterministic Google event ID, no duplicate event.
  try:
   existing=gmail._api(acct,'GET',gmail.CAL+'/calendars/primary/events/'+eid)
   return {'ok':True,'event':existing,'message':'일정이 등록되어 있어요.'}
  except gmail.GmailError as e:
   if str(e)!='not_found':raise
 conflicts=overlaps(acct,body,fields['id'])
 if conflicts:return {'ok':False,'conflicts':conflicts,'message':'겹치는 일정이 있어 등록하지 않았어요. 시간을 바꿔주세요.'}
 if fields['id']:
  data=gmail._api(acct,'PATCH',gmail.CAL+'/calendars/primary/events/'+eid,json=body,headers={'If-Match':fields['etag']},params={'sendUpdates':'none'})
 else:
  body={**body,'id':eid,'extendedProperties':{'private':{'jarvis':'true'}}}
  data=gmail._api(acct,'POST',gmail.CAL+'/calendars/primary/events',json=body,params={'sendUpdates':'none'})
 return {'ok':True,'event':data,'message':'캘린더에 반영했어요.'}


def delete_preview(email,payload):
 acct=account();eid=str(payload.get('event_id') or '')
 if not re.fullmatch(r'[a-zA-Z0-9_-]{1,1024}',eid):raise ValueError('잘못된 일정 ID')
 event=gmail._api(acct,'GET',gmail.CAL+'/calendars/primary/events/'+eid)
 if event.get('status')=='cancelled':return {'ok':False,'message':'이미 삭제된 일정이에요.'}
 if event.get('recurrence'):return {'ok':False,'message':'반복 일정은 삭제할 날짜의 한 회차를 조회해서 선택해주세요.'}
 if event.get('organizer') and not event['organizer'].get('self'):return {'ok':False,'message':'다른 사람이 주최한 일정은 Google 캘린더에서 참석 여부를 변경해주세요.'}
 if not event.get('etag'):raise ValueError('일정 버전을 확인하지 못했어요.')
 fields={'email':email,'account':acct,'id':eid,'etag':event['etag']}
 return {'ok':True,'event':{k:event.get(k) for k in ('id','summary','start','end')},'notify':bool(event.get('attendees')),'token':desktop_auth.seal('calendar-delete',fields,600)}

def delete_commit(email,token):
 fields=desktop_auth.read(token,'calendar-delete')
 if fields['email']!=email or fields['account']!=account():raise ValueError('삭제할 일정을 다시 확인해주세요.')
 acct=fields['account'];eid=fields['id'];url=gmail.CAL+'/calendars/primary/events/'+eid
 try:
  current=gmail._api(acct,'GET',url)
 except gmail.GmailError as exc:
  if str(exc)=='not_found':return {'ok':True,'deleted_id':eid,'message':'이미 삭제된 일정이에요.'}
  raise
 if current.get('status')=='cancelled':return {'ok':True,'deleted_id':eid,'message':'이미 삭제된 일정이에요.'}
 if current.get('etag')!=fields['etag']:return {'ok':False,'message':'확인 이후 일정이 변경됐어요. 다시 조회하고 삭제해주세요.'}
 gmail._api(acct,'DELETE',url,headers={'If-Match':fields['etag']},params={'sendUpdates':'all'})
 return {'ok':True,'deleted_id':eid,'message':'캘린더에서 일정을 삭제했어요.'}

def poll_mail(cursor=""):
 """No paid analysis, no history cursor advancement; retrying cannot lose mail."""
 acct=account()
 params={'q':'in:inbox newer_than:7d','maxResults':100}
 if cursor:params['pageToken']=str(cursor)
 listing=gmail._api(acct,'GET',gmail.GMAIL+'/messages',params=params)
 ids=[x['id'] for x in listing.get('messages',[])]
 known=set()
 if ids:
  known={r['gmail_id'] for r in todos._request('GET','mail_items',params={'select':'gmail_id','email':f'eq.{acct}','gmail_id':'in.('+','.join(ids)+')'}).json()}
 missing=[i for i in ids if i not in known]
 def load(mid):
  raw=gmail._api(acct,'GET',gmail.GMAIL+'/messages/'+mid,params={'format':'full'})
  m=gmail._parse_message(raw);labels=raw.get('labelIds',[])
  m['category']='promo' if 'CATEGORY_PROMOTIONS' in labels else 'fyi'
  m['summary']=m['snippet'];return m
 with ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(load,missing[:10]))
 gmail.save_items(acct,rows)
 items=todos._request('GET','mail_items',params={'select':gmail.ITEM_FIELDS,'email':f'eq.{acct}','status':'neq.dismissed','order':'received_at.desc.nullslast','limit':'100'}).json()
 return {'ok':True,'items':items,'new':len(rows),'backlog':len(missing)>10 or bool(listing.get('nextPageToken')),'next_cursor':cursor if len(missing)>10 else listing.get('nextPageToken',''),'coverage':'최근 7일 받은편지함을 페이지별 동기화; 과거 검색은 사이트 저장 메일 전체'}

def dispatch(email,op,p):
 if op=='records':return {'ok':True,**records(email,p.get('source'),p.get('offset',0))}
 if op=='status':
  acct=account();now=datetime.now(KST)
  events=event_list(acct,now.isoformat(),(now+timedelta(days=7)).isoformat())
  gmail._api(acct,'GET',gmail.GMAIL+'/profile')
  permissions=gmail.requests.get('https://oauth2.googleapis.com/tokeninfo',params={'access_token':gmail.access_token(acct)},timeout=10).json()
  scopes=permissions.get('scope','').split()
  return {'ok':True,'mail':True,'calendar':True,'trash':'https://www.googleapis.com/auth/gmail.modify' in scopes or 'https://mail.google.com/' in scopes,'events':len(events),'sources':list(SOURCES)}
 if op=='calendar.list':return {'ok':True,'items':event_list(account(),p['start'],p['end'])}
 if op=='calendar.preview':return preview(email,p)
 if op=='calendar.commit':return commit(email,p['token'])
 if op=='calendar.delete.preview':return delete_preview(email,p)
 if op=='calendar.delete.commit':return delete_commit(email,p['token'])
 if op=='calendar.verify':
  acct=account()
  proposal=preview(email,{'schedule':{'title':'자비스 연결 시험 (자동 삭제)','date':'2036-01-02','time':'03:14','duration_min':1}})
  if proposal['conflicts']:raise ValueError('시험 시간에 일정이 있어 검증을 중단했습니다.')
  token=proposal['token'];eid='jarvis'+hashlib.sha256(token.encode()).hexdigest()[:40]
  try:
   created=commit(email,token)
   if not created.get('ok'):raise ValueError('시험 일정 등록 실패')
   result=gmail._api(acct,'GET',gmail.CAL+'/calendars/primary/events/'+eid)
   if result.get('id')!=eid:raise ValueError('일정 조회 검증 실패')
   repeated=commit(email,token)
   if repeated.get('event',{}).get('id')!=eid:raise ValueError('중복 방지 검증 실패')
   conflict=overlaps(acct,event_body({'title':'겹침검증','date':'2036-01-02','time':'03:14','duration_min':1}))
   if not any(x['id']==eid for x in conflict):raise ValueError('충돌 검증 실패')
   update=preview(email,{'event_id':eid,'schedule':{'title':'자비스 연결 수정 시험 (자동 삭제)','date':'2036-01-02','time':'03:16','duration_min':1}})
   changed=commit(email,update['token'])
   if not changed.get('ok') or '수정 시험' not in changed.get('event',{}).get('summary',''):raise ValueError('수정 검증 실패')
  finally:
   try:gmail._api(acct,'DELETE',gmail.CAL+'/calendars/primary/events/'+eid,params={'sendUpdates':'none'})
   except gmail.GmailError as exc:
    if str(exc)!='not_found':raise
  return {'ok':True,'created_read_deleted':True,'retry_deduplicated':True,'conflict_checked':True,'update_checked':True}
 if op=='mail.poll':return poll_mail(p.get('cursor',''))
 if op=='mail.detail':
  acct=account();item=gmail.get_item(acct,int(p['id']))
  return {'ok':True,'item':{**item,**gmail._parse_message(gmail._api(acct,'GET',gmail.GMAIL+'/messages/'+item['gmail_id'],params={'format':'full'}))}}
 if op=='mail.analysis':
  acct=account();item=gmail.get_item(acct,int(p['id']));schedule=p.get('schedule') or {}
  event_body(schedule)
  clean={k:schedule[k] for k in ('title','date','time','duration_min','location') if k in schedule}
  todos._request('PATCH','mail_items',params={'email':f'eq.{acct}','id':f"eq.{item['id']}"},json={'schedule':clean,'category':'schedule'})
  return {'ok':True}
 if op=='mail.trash.preview':
  acct=account();item=gmail.get_item(acct,int(p['id']))
  return {'ok':True,'subject':item['subject'],'token':desktop_auth.seal('mail-trash',{'email':email,'account':acct,'id':item['id'],'gmail_id':item['gmail_id']},600)}
 if op=='mail.trash.commit':
  x=desktop_auth.read(p['token'],'mail-trash')
  if x['email']!=email or x['account']!=account():raise ValueError('메일을 다시 확인해주세요.')
  gmail._api(x['account'],'POST',gmail.GMAIL+'/messages/'+x['gmail_id']+'/trash')
  gmail.set_status(x['account'],x['id'],'dismissed')
  return {'ok':True,'message':'메일을 휴지통으로 옮겼어요.'}
 raise ValueError('지원하지 않는 요청입니다.')
