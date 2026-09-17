import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import jarvis
from api.index import app


class JarvisTest(unittest.TestCase):
    def test_explicit_task_uses_existing_store(self):
        with patch.object(jarvis.todos, 'add_todo', return_value={'title': '보고서 작성', 'due_date': '2026-09-18'}) as add:
            result = jarvis.command('owner@example.com', '자비스, 내일 보고서 작성 할 일 등록해줘')
        add.assert_called_once_with('owner@example.com', '내일 보고서 작성')
        self.assertTrue(result['ok'])

    def test_question_and_appointment_do_not_write(self):
        with patch.object(jarvis.todos, 'add_todo') as add:
            for text in ['내일 보고서 등록할까?', '내일 오후 3시 미팅 등록해줘', '내일 회의 등록해줘']:
                self.assertFalse(jarvis.command('owner@example.com', text)['ok'])
        add.assert_not_called()

    def test_briefing_reads_only_requesting_user(self):
        with patch.object(jarvis.todos, 'list_todos', return_value={'pending': []}) as read:
            result = jarvis.command('owner@example.com', '자비스 브리핑해봐')
        read.assert_called_once_with('owner@example.com')
        self.assertIn('0건', result['message'])

    def test_owner_required_even_if_auth_not_configured(self):
        client = TestClient(app)
        with patch('api.index._owner_user', return_value=None), patch.object(jarvis.todos, 'add_todo') as add:
            self.assertEqual(client.post('/api/jarvis/command', json={'text': '내일 보고서 등록해줘'}).status_code, 403)
        add.assert_not_called()

    def test_cross_origin_rejected(self):
        with patch('api.index._owner_user', return_value='owner@example.com'):
            response = TestClient(app).post('/api/jarvis/command', json={'text': '브리핑해봐'}, headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)

    def test_route_write_then_brief(self):
        client = TestClient(app)
        with patch('api.index._owner_user', return_value='owner@example.com'), patch.object(jarvis.todos, 'add_todo', return_value={'title': '보고서', 'due_date': None}), patch.object(jarvis.todos, 'list_todos', return_value={'pending': [{'title': '보고서', 'due_date': None}]}):
            self.assertTrue(client.post('/api/jarvis/command', json={'text': '보고서 등록해줘'}).json()['ok'])
            self.assertIn('1건', client.post('/api/jarvis/command', json={'text': '브리핑해봐'}).json()['message'])
