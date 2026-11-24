import tempfile
import uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import materials, store
from backend.attachment_flow import plan_attachment_tools
from backend.main import app
from backend.model_transport import ModelRequestError
from backend.recommendation_review import Claim, Interpretation, interpret_facts


def tool(name, arguments='{}'):
    return {'function': {'name': name, 'arguments': arguments}}


class AttachmentAutomationTests(TestCase):
    def test_mixed_batch_only_queues_valid_groups_and_is_idempotent(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, 'ROOT', root))
        client = self.enterContext(TestClient(app))
        files = [('files', ('模拟学生.csv', '学生编号,模拟班级编号\nS01,A\n'.encode())),
                 ('files', ('教材.pdf', b'%PDF-1.7\n')), ('files', ('伪装.pdf', b'not a pdf'))]
        body = {'batch_id': str(uuid.uuid4()), 'continue_target': 'student:S01'}
        with patch('backend.attachment_flow.complete_with_tools', return_value={'tool_calls': [
            tool('queue_material_extract'), tool('queue_material_extract'), tool('publish_material'),
            tool('queue_student_import', '{"path":"private.csv"}'),
        ]}) as completion:
            result = client.post('/api/chat/attachments/submit', files=files, data=body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), client.post('/api/chat/attachments/submit', files=files, data=body).json())
            completion.assert_called_once()
            sent = str(completion.call_args)
            self.assertNotIn('教材.pdf', sent)
            self.assertNotIn('S01', sent)
        result = result.json()
        self.assertEqual([(step['tool'], step['origin']) for step in result['trace']], [
            ('queue_material_extract', 'agent'), ('queue_student_import', 'validated_fallback')])
        self.assertEqual([card['kind'] for card in result['cards']], ['material_extract', 'cleaning', 'attachment_skipped'])
        self.assertEqual(client.get('/api/chat/sessions/' + result['session_id']).json()['turns'][0]['trace'], result['trace'])
        with store.db() as conn:
            self.assertEqual([m['source'] for m in store.all_of(conn, 'material')], ['upload'])
            self.assertEqual([m['state'] for m in store.all_of(conn, 'material')], ['processing'])
            self.assertEqual(sorted(m['kind'] for m in store.all_of(conn, 'job')), ['clean', 'extract'])
            self.assertIsNone(store.get(conn, 'settings', 'active_snapshot'))
            self.assertEqual(store.all_of(conn, 'import')[0]['continuation'], {'student':'S01'})
        self.assertEqual(len(list((root / 'files').iterdir())), 2)

    def test_tool_failure_uses_validated_group_only(self):
        with patch('backend.attachment_flow.complete_with_tools', side_effect=ModelRequestError('timeout')):
            self.assertEqual(plan_attachment_tools('test', 'session', {'queue_student_import': 0, 'queue_material_extract': 1}),
                             [('queue_material_extract', 'validated_fallback')])

    def test_image_without_ocr_reports_actionable_error(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(materials, 'ROOT', root), patch.object(materials, 'ocr_executable', return_value=root / 'missing.exe'):
            with self.assertRaisesRegex(RuntimeError, '未配置 PaddleOCR'):
                materials.process({'path':str(root / 'image.png'), 'id':'sample'})

    def test_truncation_retry_preserves_evidence_validation(self):
        facts = {'axes': {'I1':{'sample_ids':['E1'], 'n':1}}, 'anchor_event':'E1'}
        good = Interpretation(summary='本轮样本有限', claims=[Claim(indicator_id='I1', evidence_ids=['E1'], detail='继续观察')])
        with patch('backend.recommendation_review.structured_review', side_effect=[ModelRequestError('truncated'), (good, 'stub')]) as call:
            value = interpret_facts(facts, '解读', 'task', 'snapshot')
        self.assertEqual(value['claims'][0]['refs'], ['I1', 'E1'])
        self.assertEqual([invocation.kwargs['max_tokens'] for invocation in call.call_args_list], [12000, 16000])
        self.assertEqual(call.call_count, 2)
        with patch('backend.recommendation_review.structured_review', side_effect=[ModelRequestError('output_budget'), (good, 'stub')]) as call:
            interpret_facts(facts, '解读', 'task', 'snapshot')
        self.assertEqual([invocation.kwargs['max_tokens'] for invocation in call.call_args_list], [12000, 6000])
        with patch('backend.recommendation_review.structured_review', side_effect=ModelRequestError('truncated')) as call:
            with self.assertRaisesRegex(ModelRequestError, '截断'):
                interpret_facts(facts, '解读', 'task', 'snapshot')
        self.assertEqual(call.call_count, 2)
