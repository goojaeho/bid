import unittest
from unittest.mock import patch
from app import desktop_service as s
class DeleteTest(unittest.TestCase):
 def test_preview_does_not_delete(self):
  event={'id':'abc','summary':'test','start':{'date':'2036-01-02'},'etag':'v1'}
  with patch.object(s,'account',return_value='acct'),patch.object(s.gmail,'_api',return_value=event) as api,patch.object(s.desktop_auth,'seal',return_value='token'):
   self.assertTrue(s.delete_preview('owner',{'event_id':'abc'})['ok']);self.assertEqual(api.call_count,1);self.assertEqual(api.call_args.args[1],'GET')
 def test_commit_is_owner_and_version_bound(self):
  fields={'email':'owner','account':'acct','id':'abc','etag':'v1'}
  with patch.object(s,'account',return_value='acct'),patch.object(s.desktop_auth,'read',return_value=fields),patch.object(s.gmail,'_api',return_value={'etag':'v2'}) as api:
   self.assertFalse(s.delete_commit('owner','token')['ok']);self.assertEqual(api.call_count,1)
   with self.assertRaises(ValueError):s.delete_commit('other','token')
   self.assertEqual(api.call_count,1)
 def test_delete_conditional_and_repeat(self):
  fields={'email':'owner','account':'acct','id':'abc','etag':'v1'}
  with patch.object(s,'account',return_value='acct'),patch.object(s.desktop_auth,'read',return_value=fields),patch.object(s.gmail,'_api',side_effect=[{'etag':'v1'},{},s.gmail.GmailError('not_found')]) as api:
   self.assertTrue(s.delete_commit('owner','token')['ok']);self.assertEqual(api.call_args.kwargs['headers'],{'If-Match':'v1'});self.assertEqual(api.call_args.args[1],'DELETE');self.assertTrue(s.delete_commit('owner','token')['ok'])
 def test_recurring_master_and_other_organizer_blocked(self):
  for event in [{'recurrence':['RRULE:FREQ=DAILY']},{'organizer':{'self':False}}]:
   with patch.object(s,'account',return_value='acct'),patch.object(s.gmail,'_api',return_value=event):self.assertFalse(s.delete_preview('owner',{'event_id':'abc'})['ok'])
