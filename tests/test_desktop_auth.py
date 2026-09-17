import base64
import hashlib
import os
import unittest
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import auth, desktop_auth
from api.index import app


class DesktopAuthTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GOOGLE_CLIENT_ID':'test', 'GOOGLE_CLIENT_SECRET':'test-secret', 'OWNER_EMAIL':'owner@example.com'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.verifier = 'a' * 43
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')

    def test_login_exchange_and_scoped_api(self):
        client = TestClient(app)
        response = client.post('/api/desktop/start', json={'port':12345, 'nonce':'b'*64, 'challenge':self.challenge})
        state = parse_qs(urlparse(response.json()['url']).query)['state'][0]
        with patch.object(auth, 'handle_callback', return_value='owner@example.com'):
            callback = client.get('/auth/callback', params={'code':'google-code','state':state}, follow_redirects=False)
        location = urlparse(callback.headers['location'])
        self.assertEqual(location.netloc, '127.0.0.1:12345')
        grant = parse_qs(location.query)['code'][0]
        self.assertEqual(client.post('/api/desktop/token',json={'code':grant,'verifier':'z'*43}).status_code,401)
        token = client.post('/api/desktop/token',json={'code':grant,'verifier':self.verifier}).json()['token']
        self.assertIsNone(auth.read_session(token))
        with patch('app.jarvis.command',return_value={'ok':True,'message':'완료'}) as command:
            r = client.post('/api/desktop/command',headers={'Authorization':'Bearer '+token},json={'text':'브리핑해봐'})
        self.assertTrue(r.json()['ok'])
        command.assert_called_once_with('owner@example.com','브리핑해봐')

    def test_tampered_expired_wrong_kind_and_other_user(self):
        token=desktop_auth.seal('access',{'email':'owner@example.com'},30)
        self.assertIsNone(desktop_auth.owner('Bearer '+token+'x'))
        with patch('app.desktop_auth.time.time',return_value=9999999999):
            self.assertIsNone(desktop_auth.owner('Bearer '+token))
        grant=desktop_auth.seal('grant',{'email':'owner@example.com'},30)
        self.assertIsNone(desktop_auth.owner('Bearer '+grant))
        other=desktop_auth.seal('access',{'email':'other@example.com'},30)
        self.assertIsNone(desktop_auth.owner('Bearer '+other))
        self.assertIsNone(desktop_auth.owner('Bearer '+auth.make_session('owner@example.com')))

    def test_bad_state_never_exchanges_google_code(self):
        with patch.object(auth,'handle_callback') as exchange:
            response=TestClient(app).get('/auth/callback',params={'code':'x','state':'desktop.tampered'})
        self.assertEqual(response.status_code,400)
        exchange.assert_not_called()

    def test_bad_redirect_parameters(self):
        for port in [0,80,65536]:
            with self.assertRaises(ValueError):desktop_auth.start(port,'b'*64,self.challenge)
        with self.assertRaises(ValueError):desktop_auth.start(12345,'https://evil.example',self.challenge)
