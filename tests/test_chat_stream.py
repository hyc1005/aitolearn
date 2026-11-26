import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import store
from backend.chat_stream import chat_events
from backend.main import app
from backend.model_transport import ModelTransport
from backend.models import stream_with_tools
from backend.models import ToolsUnavailable


class ChatStreamTests(TestCase):
    def test_unsupported_stream_falls_back_to_regular_tool_call(self):
        from backend.chat_agent import run_chat
        from backend.learning_chat import ChatRequest
        events=[]
        token=chat_events.set(events.append)
        try:
            with patch('backend.chat_agent.stream_with_tools', side_effect=ToolsUnavailable('unsupported')), \
                    patch('backend.chat_agent.complete_with_tools', return_value={'role':'assistant','content':'{"answer":"你好","quotes":[]}'}) as regular:
                result=run_chat(ChatRequest(question='你好'), None, 'opencode')
        finally:
            chat_events.reset(token)
        self.assertEqual(result['answer'], '你好')
        self.assertEqual(events[0]['type'], 'reasoning_unavailable')
        regular.assert_called_once()

    def test_reasoning_is_forwarded_only_within_tool_turn(self):
        from backend.chat_agent import run_chat
        from backend.learning_chat import ChatRequest
        seen=[]
        def provider(_provider, messages, _session, **_kwargs):
            seen.append(json.loads(json.dumps(messages)))
            if len(seen) == 1:
                return {'role':'assistant', 'content':None, 'reasoning_content':'临时思考',
                        'tool_calls':[{'id':'c1', 'function':{'name':'read_attachments','arguments':'{}'}}]}
            return {'role':'assistant', 'content':'{"answer":"没有附件","quotes":[]}'}
        token=chat_events.set(lambda _: None)
        try:
            with patch('backend.chat_agent.stream_with_tools', side_effect=provider):
                run_chat(ChatRequest(question='查看本轮附件'), None, 'opencode')
        finally:
            chat_events.reset(token)
        self.assertEqual(seen[1][2]['reasoning_content'], '临时思考')

    def test_reasoning_and_fragmented_tool_call_are_collected(self):
        chunks = [
            {'choices':[{'index':0,'delta':{'reasoning_content':'先查看'}}]},
            {'choices':[{'index':0,'delta':{'reasoning_content':'资料','tool_calls':[{'index':0,'id':'call1','function':{'name':'search_', 'arguments':'{"query":'}}]}}]},
            {'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'name':'materials', 'arguments':'"数学"}'}}]}}]},
        ]
        events=[]
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for index, chunk in enumerate(chunks):
                    if index == 1:
                        self.assert_reasoning_was_emitted()
                    yield ('data: '+json.dumps(chunk, ensure_ascii=False)+'\n\n').encode()
                yield b'data: [DONE]\n\n'
            def assert_reasoning_was_emitted(self):
                if not events:
                    raise AssertionError('reasoning must arrive before the stream ends')
        def transport(provider, model, session, **kwargs):
            kwargs.pop('transport', None)
            return ModelTransport(provider, model, session, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Stream())), **kwargs)
        token=chat_events.set(events.append)
        try:
            with patch('backend.models.provider_key', return_value='fake'), patch('backend.models.ModelTransport', side_effect=transport):
                result=stream_with_tools('opencode', [], 'session', tools=[])
        finally:
            chat_events.reset(token)
        self.assertEqual(''.join(x['text'] for x in events), '先查看资料')
        self.assertEqual(result['reasoning_content'], '先查看资料')
        self.assertEqual(result['tool_calls'][0]['function'], {'name':'search_materials','arguments':'{"query":"数学"}'})

    def test_stream_does_not_store_reasoning(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, 'ROOT', root))
        def fake_model(*args, **kwargs):
            from backend.chat_stream import emit
            emit('reasoning', text='ephemeral-secret')
            return {'role':'assistant', 'content':'{"answer":"结果","quotes":[]}'}
        with patch('backend.main.provider_key', return_value='fake'), patch('backend.chat_agent.stream_with_tools', side_effect=fake_model), TestClient(app) as client:
            response=client.post('/api/learning-chat/stream', json={'question':'你好', 'use_tools':True})
        self.assertIn('ephemeral-secret', response.text)
        with store.db() as conn:
            sessions = store.all_of(conn, 'chat_session')
            self.assertEqual(len(sessions), 1)
            self.assertNotIn('ephemeral-secret', str(sessions))
