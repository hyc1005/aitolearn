import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import httpx
from scripts.agent_cli import main


class AgentCliTests(TestCase):
    def test_chat_uses_agent_tools_and_session(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'answer': 'ok'})
        original = httpx.Client
        def client(**kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)
        with patch('scripts.agent_cli.httpx.Client', side_effect=client), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['chat', '查询任务', '--session', 'session-1']), 0)
        self.assertEqual(captured, [{'question': '查询任务', 'session_id': 'session-1', 'use_tools': True}])

    def test_request_upload_and_machine_readable_response(self):
        captured = []
        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={'received': True})
        original = httpx.Client
        def client(**kwargs):
            return original(transport=httpx.MockTransport(handler), **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'example.txt'
            file.write_text('example', encoding='utf-8')
            output = io.StringIO()
            with patch('scripts.agent_cli.httpx.Client', side_effect=client), contextlib.redirect_stdout(output):
                code = main(['request', 'POST', '/api/example', '--field', 'batch=abc', '--file', f'files={file}'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['data'], {'received': True})
        self.assertEqual(captured[0].url.host, '127.0.0.1')
        self.assertIn(b'example', captured[0].content)
        self.assertIn(b'abc', captured[0].content)

    def test_rejects_external_and_traversal_paths(self):
        for path in ['https://example.com/api/a', '//example.com/api/a', '/api/../other']:
            with self.subTest(path=path), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(['request', 'GET', path])
