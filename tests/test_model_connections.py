import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store
from backend.main import app


class CompatibleService(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.requests.append((self.path, self.headers['Authorization'], None))
        if self.server.hide_catalog:
            self.send_json({'error': 'missing catalog'}, 404)
        else:
            self.send_json({'data': [{'id': name} for name in ('multi', 'toggle')]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, self.headers['Authorization'], body))
        if body.get('reasoning_effort') == 'max':
            self.send_json({'error': {'param': 'reasoning_effort', 'message': 'unsupported'}}, 400)
        elif body.get('tools') and body['model'] == 'toggle':
            self.send_json({'error': 'tools not supported'}, 400)
        elif body.get('tools') and len(body['messages']) == 2:
            self.send_json({'choices': [{'message': {'content': None, 'reasoning_content': 'round-state', 'tool_calls': [
                {'id': 't1', 'type': 'function', 'function': {'name': 'read_jobs', 'arguments': '{}'}}]}}]})
        else:
            self.send_json({'choices': [{'message': {'content': '{"answer":"OK","quotes":[]}'}}]})


class ModelConnectionsTest(TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(store, 'ROOT', Path(directory)))
        credentials = {}
        self.enterContext(patch('backend.models.keyring.get_password', side_effect=lambda _, key: credentials.get(key)))
        self.enterContext(patch('backend.models.keyring.set_password', side_effect=lambda _, key, value: credentials.update({key: value})))
        self.upstream = self.enterContext(ThreadingHTTPServer(('127.0.0.1', 0), CompatibleService))
        self.upstream.requests, self.upstream.hide_catalog = [], False
        thread = Thread(target=self.upstream.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.upstream.shutdown)
        self.client = self.enterContext(TestClient(app))
        self.endpoint = f'http://127.0.0.1:{self.upstream.server_port}/v1'
        self.secret = 'test-bound-credential'
        saved = self.client.put('/api/models/connections/new', json={
            'name': 'local', 'base_url': self.endpoint, 'api_key': self.secret})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(self.secret, saved.text)
        self.cid = saved.json()['saved_id']
        self.path = '/api/models/connections/' + self.cid

    def register(self, model, mode, levels=(), check=True):
        response = self.client.put(self.path + '/model', json={
            'model_id': model, 'enabled': check, 'mode': mode, 'efforts': levels})
        self.assertEqual(response.status_code, 200, response.text)

    def probe(self, model, effort='default'):
        return self.client.post(self.path + '/test', json={'model_id': model, 'effort': effort})

    def send(self, model=None, effort=None, session_id='', tools=True):
        body = {'session_id': session_id, 'question': 'Say hello', 'use_tools': tools, 'use_configured_models': True}
        if model is not None:
            body.update(connection_id=self.cid, model_id=model, effort=effort)
        return self.client.post('/api/learning-chat', json=body)

    def test_switch_rounds_and_restore_selection_and_preserve_fallback(self):
        self.assertEqual(self.client.post(self.path + '/catalog').status_code, 200)
        self.register('multi', 'effort', ['low', 'high', 'max'])
        self.register('toggle', 'switch')
        self.assertEqual(self.send('multi', 'default').status_code, 422)
        for model, effort in [('multi', 'default'), ('multi', 'low'), ('multi', 'high'), ('toggle', 'default'), ('toggle', 'on'), ('toggle', 'off')]:
            self.assertEqual(self.probe(model, effort).status_code, 200)
        denied = self.probe('multi', 'max')
        self.assertEqual(denied.status_code, 502)
        self.assertIn('reasoning_effort', denied.json()['detail'])
        self.assertEqual(self.client.put('/api/models/chat-default/' + self.cid, json={'model_id': 'multi'}).status_code, 200)
        self.upstream.requests.clear()
        first = self.send('multi', 'high')
        self.assertEqual(first.status_code, 200, first.text)
        sid = first.json()['session_id']
        second = self.send('toggle', 'off', sid)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['mode'], 'without_tools')
        bodies = [b for _, _, b in self.upstream.requests]
        self.assertEqual([b['model'] for b in bodies], ['multi', 'multi', 'toggle', 'toggle'])
        self.assertTrue(all(b['reasoning_effort'] == 'high' for b in bodies[:2]))
        self.assertEqual(bodies[1]['messages'][-2]['reasoning_content'], 'round-state')
        self.assertTrue(all(b['thinking'] == {'type': 'disabled'} for b in bodies[2:]))
        self.assertNotIn('tools', bodies[-1])
        path = '/api/chat/sessions/' + sid
        saved = self.client.get(path).json()
        self.assertEqual([(t['model_call']['model_id'], t['model_call']['effort']) for t in saved['turns']],
                         [('multi', 'high'), ('toggle', 'off')])
        self.assertEqual(saved['selection']['model_id'], 'toggle')
        changed = self.client.put(path + '/selection', json={'connection_id': self.cid, 'model_id': 'multi', 'effort': 'low'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.send(session_id=sid, tools=False).json()['model_call']['effort'], 'low')
        self.register('multi', 'effort', ['low', 'high', 'max'], check=False)
        self.assertEqual(self.send(session_id=sid).status_code, 422)
        self.assertEqual(len(self.client.get(path).json()['turns']), 3)

    def test_manual_unknown_catalog_failure_and_bound_address(self):
        self.assertEqual(self.client.post(self.path + '/catalog').status_code, 200)
        self.upstream.hide_catalog = True
        self.assertEqual(self.client.post(self.path + '/catalog').status_code, 502)
        self.register('manual-only', 'unknown')
        self.assertEqual(self.probe('manual-only').status_code, 200)
        self.assertEqual(self.send('manual-only', 'default', tools=False).status_code, 200)
        for url, auth, body in self.upstream.requests:
            self.assertEqual(auth, 'Bearer ' + self.secret)
            self.assertTrue(url.startswith('/v1/'))
            if body:
                self.assertNotIn('reasoning_effort', body)
                self.assertNotIn('thinking', body)
        self.assertEqual(self.client.put(self.path, json={'name': 'other', 'base_url': self.endpoint + '2'}).status_code, 422)
        listed = self.client.get('/api/models/connections').json()
        custom = next(item for item in listed['connections'] if item['id'] == self.cid)
        self.assertEqual(custom['catalog'], ['multi', 'toggle'])
        self.assertTrue(custom['models'][0]['enabled'])
