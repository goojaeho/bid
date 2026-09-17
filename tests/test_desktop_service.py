import unittest
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
from api.index import app
from app import desktop_service as s, desktop_auth

class GatewayTest(unittest.TestCase):
 def test_owner_required(self):
  self.assertEqual(TestClient(app).post('/api/desktop/service',json={'op':'records','source':'mail'}).status_code,401)
 def test_only_requested_sources_and_owner_filter(self):
  for source in ['chats','favs','todos','places','searches']:
   with self.assertRaises(ValueError):s.records('owner',source)
  with patch.object(s.todos,'_request',return_value=Mock(json=lambda:[{'id':1,'title':'비공개','transcript':'회의 원문'}])) as request:
   r=s.records('owner','meetings',25)
   self.assertEqual(request.call_args.kwargs['params']['email'],'eq.owner')
   self.assertEqual(request.call_args.kwargs['params']['offset'],'25')
   self.assertIn('회의 원문',r['items'][0]['text'])
 def test_all_day_exclusive_and_time_validation(self):
  self.assertEqual(s.event_body({'title':'종일','date':'2026-12-31'})['end']['date'],'2027-01-01')
  body=s.event_body({'title':'야간','date':'2026-09-17','time':'23:30','duration_min':90})
  self.assertTrue(body['end']['dateTime'].startswith('2026-09-18T01:00'))
  for p in [{'title':'x','date':'2026-02-30'},{'title':'x','date':'2026-01-01','time':'25:00'},{'title':'x','date':'2026-01-01','time':'09:00','duration_min':-1}]:
   with self.assertRaises(ValueError):s.event_body(p)
 def test_commit_rechecks_conflict_without_write(self):
  fields={'email':'owner','account':'acct','body':s.event_body({'title':'회의','date':'2026-09-18'}),'id':'','nonce':'x'}
  with patch.object(desktop_auth,'read',return_value=fields),patch.object(s,'account',return_value='acct'),patch.object(s,'overlaps',return_value=[{'id':'busy'}]),patch.object(s.gmail,'_api',side_effect=s.gmail.GmailError('not_found')) as api:
   r=s.commit('owner','token');self.assertFalse(r['ok']);self.assertEqual(api.call_count,1)
 def test_duplicate_token_returns_existing(self):
  fields={'email':'owner','account':'acct','body':{},'id':''}
  with patch.object(desktop_auth,'read',return_value=fields),patch.object(s,'account',return_value='acct'),patch.object(s.gmail,'_api',return_value={'id':'existing'}) as api:
   self.assertTrue(s.commit('owner','token')['ok']);self.assertEqual(api.call_count,1);self.assertEqual(api.call_args.args[1],'GET')
 def test_cross_owner_commit_rejected(self):
  with patch.object(desktop_auth,'read',return_value={'email':'other'}),patch.object(s.gmail,'_api') as api:
   with self.assertRaises(ValueError):s.commit('owner','token')
   api.assert_not_called()
 def test_conflict_pagination_and_ignored_free_events(self):
  pages=[{'items':[{'id':'a','transparency':'transparent'}],'nextPageToken':'next'},{'items':[{'id':'b','status':'confirmed'},{'id':'c','attendees':[{'self':True,'responseStatus':'declined'}]}]}]
  with patch.object(s.gmail,'_api',side_effect=pages) as api:
   r=s.overlaps('acct',s.event_body({'title':'회의','date':'2026-09-18'}))
   self.assertEqual([x['id'] for x in r],['b']);self.assertEqual(api.call_count,2)
 def test_oauth_callback_requires_state_before_exchange(self):
  with patch('api.index._admin_user',return_value='owner'),patch.object(s.gmail,'handle_callback') as exchange:
   r=TestClient(app).get('/gmail/callback?code=fake')
   self.assertEqual(r.status_code,400);exchange.assert_not_called()

 def test_mail_analysis_stays_owner_scoped_and_does_not_execute_calendar(self):
  with patch.object(s,'account',return_value='acct'),patch.object(s.gmail,'get_item',return_value={'id':7}),patch.object(s.todos,'_request') as db,patch.object(s.gmail,'_api') as google:
   r=s.dispatch('owner','mail.analysis',{'id':7,'schedule':{'title':'회의','date':'2026-09-18','time':'15:00','attendees':['stranger']}})
   self.assertTrue(r['ok']);self.assertNotIn('attendees',db.call_args.kwargs['json']['schedule'])
   self.assertEqual(db.call_args.kwargs['params']['email'],'eq.acct');google.assert_not_called()
 def test_trash_requires_bound_owner_token(self):
  with patch.object(desktop_auth,'read',return_value={'email':'someone-else'}),patch.object(s.gmail,'_api') as api:
   with self.assertRaises(ValueError):s.dispatch('owner','mail.trash.commit',{'token':'bad'})
   api.assert_not_called()

if __name__=='__main__':unittest.main()
