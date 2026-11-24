import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store, workflow
from backend.main import app


class AttachmentWorkflowTests(TestCase):
    def setUp(self):
        self.enterContext(patch.object(store, 'ROOT', Path(self.enterContext(tempfile.TemporaryDirectory()))))
        self.client = self.enterContext(TestClient(app))

    def inspect(self, name, body, reference=False):
        result = self.client.post('/api/chat/attachments/inspect', files={'file': (name, body)},
                                  data={'reference': str(reference).lower()})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_classification_uses_contents_and_does_not_publish(self):
        roster = '学生编号,模拟班级编号\nD01,A\n'.encode()
        self.assertEqual(self.inspect('模拟学生.csv', roster)['classification'], 'data')
        self.assertEqual(self.inspect('教材.pdf', roster)['classification'], 'unknown')
        self.assertEqual(self.inspect('教材.pdf', b'%PDF-1.7\n')['classification'], 'material')
        self.assertEqual(self.inspect('broken.xlsx', b'something')['classification'], 'unknown')
        ref = self.inspect('sample.txt', b'only read the uploaded file', reference=True)
        self.assertEqual(ref['excerpt'], 'only read the uploaded file')
        with store.db() as conn:
            self.assertEqual(store.all_of(conn, 'import'), [])
            self.assertEqual(store.all_of(conn, 'material'), [])

    def test_recommendation_queues_only_after_snapshot_confirmation(self):
        files = {'files': ('模拟学生.csv', '学生编号,模拟班级编号\nD01,A\n'.encode())}
        staged = self.client.post('/api/imports', files=files, data={'continue_target': 'student:D01'}).json()
        with store.db() as conn:
            item = store.get(conn, 'job', staged['run_id'])
        with patch.object(workflow, 'review_import', return_value={'decision': 'ready'}):
            workflow.run_cleaning(item)
        with store.db() as conn:
            self.assertEqual(len(store.all_of(conn, 'job')), 1)
            store.put(conn, 'job', item['id'], {**item, 'state': 'done'})
        self.assertGreaterEqual(item['stage_timings']['clean_review']['elapsed_ms'], 0)
        params = {'run_id': staged['run_id']}
        path = f"/api/imports/{staged['batch_id']}/confirm"
        with patch('backend.main.api_key', return_value='synthetic'):
            result = self.client.post(path, params=params)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), self.client.post(path, params=params).json())
        with store.db() as conn:
            jobs = [r for r in store.all_of(conn, 'job') if r['kind'] == 'generate']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['payload']['snapshot_id'], result.json()['snapshot_id'])
        self.assertEqual(jobs[0]['id'], result.json()['recommendation_run_id'])
