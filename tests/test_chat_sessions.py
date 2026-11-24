import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store
from backend.conversation import MAX_TURNS, save_session
from backend.main import app


class ChatSessionTitles(TestCase):
    def setUp(self):
        self.enterContext(patch.object(store, 'ROOT', Path(self.enterContext(tempfile.TemporaryDirectory()))))
        self.client = self.enterContext(TestClient(app))

    def test_legacy_order_and_rename_do_not_change_activity(self):
        with store.db() as conn:
            store.put(conn, 'chat_session', 'older', {'id': 'older', 'created': 1,
                      'turns': [{'question': '第一次提问', 'at': 12}], 'selection': {'model_id': 'chosen'}})
            store.put(conn, 'chat_session', 'newer', {'id': 'newer', 'updated': 15,
                      'turns': [{'question': '第二次提问'}]})
        path = '/api/chat/sessions/older'
        self.assertEqual(self.client.put(path + '/title', json={'title': ' '}).status_code, 422)
        self.assertEqual(self.client.put(path + '/title', json={'title': '学' * 73}).status_code, 422)
        self.assertEqual(self.client.put('/api/chat/sessions/missing/title', json={'title': '名称'}).status_code, 404)
        renamed = self.client.put(path + '/title', json={'title': '  阅读分析  '})
        self.assertEqual(renamed.json(), {'id': 'older', 'title': '阅读分析', 'custom_title': '阅读分析', 'last_active_at': 12})
        recent = self.client.get('/api/chat/sessions').json()
        self.assertEqual([s['id'] for s in recent], ['newer', 'older'])
        detail = self.client.get(path).json()
        self.assertEqual(detail['title'], '阅读分析')
        self.assertEqual(detail['selection']['model_id'], 'chosen')
        self.assertEqual(detail['turns'][0]['question'], '第一次提问')
        reset = self.client.put(path + '/title', json={'title': None}).json()
        self.assertEqual((reset['title'], reset['last_active_at']), ('第一次提问', 12))

    def test_title_survives_follow_up_turns_and_trimming(self):
        request = SimpleNamespace(student='', group='', node='', question='最初的话题', provider=None)
        with store.db() as conn:
            session = save_session(conn, None, request, {})
        key = session['id']
        path = f'/api/chat/sessions/{key}'
        self.client.put(path + '/title', json={'title': '复习安排'}).raise_for_status()
        request.question = '后续问题'
        with patch('backend.conversation.time.time', return_value=session['updated'] + 10), store.db() as conn:
            for _ in range(MAX_TURNS):
                save_session(conn, key, request, {})
        detail = self.client.get(path).json()
        self.assertEqual(detail['turns'][0]['question'], '后续问题')
        self.assertEqual(detail['title'], '复习安排')
        self.assertGreater(detail['last_active_at'], session['updated'])
        self.assertEqual(self.client.put(path + '/title', json={'title': None}).json()['title'], '最初的话题')
