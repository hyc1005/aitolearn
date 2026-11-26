import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store, student_query
from backend.data import TABLES, profile
from backend.main import app
from backend.search import backfill_index, search
from backend.snapshots import table_data


def example(sid, score):
    t = {name: {} for name in TABLES}
    t['总分'] = []
    t['模拟学生'] = {'D01': {'学生编号': 'D01', '模拟班级编号': 'A'}}
    t['课程要求'] = {'T01I1': {'主题编号': 'T01', '观察指标': '识别同类项、去括号并化简'},
                    'T01I2': {'主题编号': 'T01', '观察指标': '整式验算'}}
    t['测验节点'] = {node: {'节点编号': node, '可用题目编号': 'Q1', '模拟测验日期': when}
                     for node, when in [('N01', '2026-01-10'), ('N02', '2026-02-10'), ('N03', '2026-03-10')]}
    t['题目与材料'] = {'Q1': {'材料编号': 'Q1', '题型': '解答题', '主题编号': 'T01', '指标编号': 'T01I1、T01I2'}}
    for event, node, state, mark, rev in [('E1', 'N01', '已评分', str(score), str(score + 1)),
                                          ('E2', 'N02', '已评分', '3', '1'), ('E3', 'N03', '待评分', '', '1'),
                                          ('E4', 'N03', '未作答', '', '1')]:
        t['作答记录'][event] = {'作答事件编号': event, '材料编号': 'Q1', '学生编号': 'D01', '节点编号': node,
                              '评分状态': state, '主题编号': 'T01', '指标': ['T01I1', 'T01I2'],
                              '修订版本': rev, '得分': mark, '满分': '4',
                              '来源': {'source': 'test.csv', 'sheet': '作答记录', 'line': 2}}
    return {'id': sid, 'tables': t, 'report': {'valid_scored': 2}}


def material(mid, status, text):
    return {'id': mid, 'state': status, 'name': mid, 'previews': {}, 'blocks': [
        {'id': mid + '-p2', 'title': '整式教材', 'locator': 'PDF 第 2 页', 'text': text, 'vector': [1, 0]}]}


class PersistentQueriesTest(TestCase):
    def setUp(self):
        self.enterContext(patch.object(store, 'ROOT', Path(self.enterContext(tempfile.TemporaryDirectory()))))

    def test_progress_does_not_cite_unrelated_material_matching_generic_words(self):
        snap = example('active', 1)
        with store.db() as conn:
            store.put(conn, 'snapshot', snap['id'], snap)
            store.put(conn, 'import', 'batch', {'confirmed': True, 'snapshot_id': snap['id']})
            store.put(conn, 'settings', 'active_snapshot', {'id': snap['id'], 'review_run_id': 'test-review'})
            store.put(conn, 'material', 'unrelated', material('unrelated', 'published', '教材介绍三角形的面积。'))
        with patch.dict('os.environ', {'APP_KEYWORD_ONLY': '1'}), TestClient(app) as client:
            response = client.post('/api/learning-chat', json={'question': 'D01 去括号有进步吗？教材怎么讲？'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['parts'], [])
            self.assertIn('暂无匹配的已发布资料', response.json()['answer'])

    def test_backfill_parity_revision_dedup_and_keyword_independent_progress(self):
        old, active = example('old', 0), example('new', 1)
        with store.db() as conn:
            for snap in [old, active]:
                store.put(conn, 'snapshot', snap['id'], snap)
                store.put(conn, 'import', snap['id'], {'confirmed': True, 'snapshot_id': snap['id']})
            store.put(conn, 'snapshot', 'unconfirmed', example('unconfirmed', 2))
            store.put(conn, 'settings', 'active_snapshot', {'id': 'new', 'review_run_id': 'test-review'})
            store.put(conn, 'material', 'current', material('current', 'published', '去括号前先看括号前的符号。'))
        student_query.backfill(store.db)
        student_query.backfill(store.db)
        with store.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM snapshot_attempt').fetchone()[0], 8)
            self.assertEqual(sorted(tuple(row) for row in conn.execute('SELECT * FROM indexed_snapshot')),
                             [('new', 4, 2, 4, 8), ('old', 4, 2, 3, 8)])
            for snap in [old, active]:
                tbl = table_data(snap)
                self.assertEqual(student_query.profile(conn, snap['id'], tbl, student='D01'), profile(tbl, student='D01'))
        with patch.dict('os.environ', {'APP_KEYWORD_ONLY': '1'}), patch('backend.search.encode', side_effect=AssertionError), TestClient(app) as client:
            self.assertEqual(client.get('/api/profiles', params={'snapshot_id': 'unconfirmed'}).status_code, 404)
            opts = {'node': 'N01', 'student': 'D01', 'indicator': 'T01I1'}
            for sid, score in [('old', '0'), ('new', '1')]:
                answer = client.get('/api/profiles/evidence', params={**opts, 'snapshot_id': sid}).json()[0]
                self.assertEqual((answer['得分'], answer['来源']['line']), (score, 2))
            progress = client.post('/api/learning-chat', json={'question': 'D01 去括号有进步吗？', 'use_tools': True}).json()
            self.assertIn('+50.0 个百分点', progress['answer'])
            self.assertIn('待评分 1 条，未作答 1 条', progress['answer'])
            self.assertEqual(progress['parts'], [])
            mixed = client.post('/api/learning-chat', json={'question': 'D01 去括号有进步吗？教材怎么讲？'}).json()
            self.assertEqual([entry['tool'] for entry in mixed['trace']], ['read_progress', 'search_materials'])
            self.assertEqual(mixed['parts'][0]['source']['citation']['page'], 2)
            followup = client.post('/api/learning-chat', json={'session_id': progress['session_id'], 'question': '教材怎么讲这个知识点？'}).json()
            self.assertTrue(followup['parts'])

    def test_backfill_and_replace_keep_pinned_sources_without_retokenizing(self):
        old = material('old', 'published', '去括号先变号。')
        with store.db() as conn:
            conn.execute('INSERT INTO records VALUES (?,?,?)', ('material', old['id'], json.dumps(old)))
        backfill_index(store.db)
        backfill_index(store.db)
        with store.db() as conn:
            store.put(conn, 'material', 'old', {**old, 'state': 'replaced'})
            store.put(conn, 'material', 'new', material('new', 'published', '去括号要检查前面的负号。'))
            store.put(conn, 'material', 'draft', material('draft', 'review', '去括号待修改'))
        with store.db() as conn, patch.dict('os.environ', {'APP_KEYWORD_ONLY': '1'}):
            sql = []
            conn.set_trace_callback(sql.append)
            self.assertEqual([c['material'] for c in search(conn, '去括号')], ['new'])
            pinned = search(conn, '去括号', material_ids=['old'])[0]
            self.assertEqual((pinned['id'], pinned['material_version'], pinned['page']), ('old-p2', 'old', 2))
            self.assertFalse(any(stmt.lstrip().upper().startswith(('CREATE', 'INSERT', 'DELETE')) for stmt in sql))
        with store.db() as conn, patch.dict('os.environ', {'APP_KEYWORD_ONLY': '0'}), patch('backend.search.encode', return_value=[[1, 0]]):
            self.assertEqual([c['material'] for c in search(conn, 'vector only')], ['new'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/materials/old').json()['blocks'][0]['text'], '去括号先变号。')
