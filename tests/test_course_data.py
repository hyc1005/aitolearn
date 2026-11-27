import hashlib
import io
import json
import tempfile
from pathlib import Path
from openpyxl import Workbook
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from backend import course_data, course_advice, course_agent, store
from backend.learning_chat import ChatRequest


def invoke(coro):
    try:
        coro.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coro.close()
    raise AssertionError('unexpected asynchronous suspension')


def upload(data):
    class MemoryUpload:
        filename = 'scores.csv'

        async def read(self, size):
            return data[:size]
    return MemoryUpload()


class CourseDataTests(TestCase):
    def setUp(self):
        self.enterContext(patch.object(store, 'ROOT', Path(self.enterContext(tempfile.TemporaryDirectory()))))
        self.course = course_data.create_course(course_data.Course(name='语文'))

    def test_preview_decimal_and_import_trash_restore(self):
        raw = '学号,姓名,成绩,满分,评价\n01,小明,3.5,5,进步\n02,小红,0,5,\n'.encode()
        parsed = course_data.parse('scores.csv', raw)
        self.assertEqual(parsed['issues'], [])
        self.assertEqual(parsed['rows'][0]['score'], '3.5')
        result = invoke(course_data.import_file(self.course['id'], '一班', json.dumps(parsed['mapping']), hashlib.sha256(raw).hexdigest(), upload(raw)))
        self.assertEqual(result['count'], 2)
        self.assertEqual(len(course_data.records(self.course['id'])), 2)
        course_data.change_batch(result['batch_id'], 'trash')
        self.assertEqual(course_data.records(self.course['id']), [])
        course_data.change_batch(result['batch_id'], 'restore')
        self.assertEqual(len(course_data.records(self.course['id'])), 2)
        other = course_data.create_course(course_data.Course(name='历史'))
        self.assertEqual(course_data.records(other['id']), [])

    def test_invalid_rows_and_identity_conflict_are_atomic(self):
        raw = '学号,姓名,成绩,满分\n1,甲,6,5\n'.encode()
        self.assertEqual(len(course_data.parse('scores.csv', raw)['issues']), 1)
        initial = '学号,姓名,成绩\n1,甲,2\n'.encode()
        mapping = course_data.parse('scores.csv', initial)['mapping']
        invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(initial).hexdigest(), upload(initial)))
        conflict = '学号,姓名,成绩\n2,丙,3\n1,乙,2\n'.encode()
        with self.assertRaises(HTTPException):
            invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(conflict).hexdigest(), upload(conflict)))
        self.assertEqual(len(course_data.records(self.course['id'])), 1)
        self.assertEqual(len(course_data.batches(self.course['id'])), 1)

    def test_comment_and_missing_data(self):
        data = course_data.parse('scores.csv', '姓名,评价\n小明,表达清楚\n'.encode())
        self.assertEqual(data['rows'][0]['comment'], '表达清楚')
        self.assertIsNone(data['rows'][0]['score'])
        self.assertTrue(course_data.parse('scores.csv', '姓名,其他\n小明,x\n'.encode())['missing'])

    def test_legacy_comment_migration_is_idempotent(self):
        raw = '学号,评价\n1,表达清楚\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        with store.db() as conn:
            conn.execute("UPDATE cd_record SET kind='total'")
        course_data.migrate_comments()
        course_data.migrate_comments()
        self.assertEqual(course_data.records(self.course['id'])[0]['kind'], 'comment')

    def test_same_name_without_identifier_never_silently_merges(self):
        raw = '姓名,评价\n小明,积极\n小明,认真\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        with self.assertRaises(HTTPException):
            invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        self.assertEqual(course_data.records(self.course['id']), [])

    def test_multisheet_wide_import_and_identity_choices(self):
        book = Workbook()
        book.active.title = '成绩'
        book.active.append(['学号', '姓名', '总分', '第1题', '第2题'])
        book.active.append(['01', '小明', 3.5, 1.5, 2])
        sheet = book.create_sheet('评价')
        sheet.append(['学号', '姓名', '评价'])
        sheet.append(['01', '小明', '表达清楚'])
        stream = io.BytesIO()
        book.save(stream)
        raw = stream.getvalue()
        config = {'sheets': [
            {'sheet': '成绩', 'mapping': {'student_id':'学号','student_name':'姓名','score':'总分'}, 'wide_columns':['第1题','第2题']},
            {'sheet': '评价', 'mapping': {'student_id':'学号','student_name':'姓名','comment':'评价'}, 'wide_columns':[]},
        ]}
        parsed = course_data.parse_selection('scores.xlsx', raw, config)
        self.assertEqual(len(parsed['rows']), 4)
        self.assertEqual([row.get('question', '') for row in parsed['rows']], ['', '第1题', '第2题', ''])
        source = upload(raw)
        source.filename = 'scores.xlsx'
        result = invoke(course_data.import_file(self.course['id'], '一班', json.dumps(config), hashlib.sha256(raw).hexdigest(), source))
        self.assertEqual(result['count'], 4)
        records = course_data.records(self.course['id'])
        self.assertEqual(len({row['student_id'] for row in records}), 1)
        with store.db() as conn:
            candidates = course_data.identity_candidates(conn, '一班', parsed['rows'])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]['candidates'][0]['external_id'], '01')

    def test_wide_maximum_and_activity_validation(self):
        raw = '学号,第1题\n01,1.5\n'.encode()
        config = {'sheets': [{'sheet':'', 'mapping':{'student_id':'学号'}, 'wide_columns':['第1题'],
                              'wide_maximums':{'第1题':'2'}, 'activity':'期中'}]}
        result = course_data.parse_selection('scores.csv', raw, config)
        self.assertEqual(result['rows'][0]['maximum'], '2')
        self.assertEqual(result['rows'][0]['activity'], '期中')
        config['sheets'][0]['wide_maximums']['第1题'] = '1'
        self.assertIn('超过满分', result := course_data.parse_selection('scores.csv', raw, config)['issues'][0]['reason'])

    def test_course_profile_and_chat_exclude_trashed_records(self):
        raw = '学号,姓名,成绩,评价\n01,小明,3.5,表达清楚\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        result = invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        profile = course_data.course_profile(self.course['id'])
        student = profile['students'][0]['id']
        answer, count = course_data.course_chat_answer(self.course['id'], student, '查看小明成绩')
        self.assertEqual(count, 1)
        self.assertIn('3.5', answer)
        self.assertIn('未提供满分', answer)
        self.assertIn('表达清楚', answer)
        course_data.change_batch(result['batch_id'], 'trash')
        self.assertEqual(course_data.course_profile(self.course['id'])['records'], [])
        with self.assertRaises(HTTPException):
            course_data.course_profile(self.course['id'], student)

    def test_advice_review_version_and_trashed_data(self):
        raw = '学号,姓名,成绩,满分\n01,小明,3.5,5\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        batch = invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        student = course_data.course_profile(self.course['id'])['students'][0]['id']
        draft = course_advice.AdviceText(observation='本次得分3.5分', suggestion='核对本次题目', limitations='仅一次记录')
        review = course_advice.Review(decision='pass', explanation='符合现有记录')
        with patch('backend.course_advice.structured_review', side_effect=[(draft, 'test'), (review, 'test')]):
            item = course_advice.generate_advice(self.course['id'], course_advice.AdviceScope(student_id=student))
            item = course_advice.review_advice(item['id'], course_advice.Revision(revision=1))
        self.assertEqual(item['state'], 'reviewed')
        course_data.change_batch(batch['batch_id'], 'trash')
        with self.assertRaises(HTTPException):
            course_advice.confirm_advice(item['id'], course_advice.Revision(revision=1))

    def test_advice_edit_requires_new_review_before_confirmation(self):
        raw = '学号,姓名,成绩,满分\n01,小明,3.5,5\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        student = course_data.course_profile(self.course['id'])['students'][0]['id']
        draft = course_advice.AdviceText(observation='本次得分3.5分', suggestion='核对本次题目', limitations='仅一次记录')
        review = course_advice.Review(decision='pass', explanation='符合现有记录')
        with patch('backend.course_advice.structured_review', side_effect=[(draft, 'test'), (review, 'test'), (review, 'test')]):
            item = course_advice.generate_advice(self.course['id'], course_advice.AdviceScope(student_id=student))
            item = course_advice.review_advice(item['id'], course_advice.Revision(revision=1))
            item = course_advice.edit_advice(item['id'], course_advice.EditAdvice(revision=1, observation='观察3.5分', suggestion='核对题目', limitations='一次记录'))
            with self.assertRaises(HTTPException):
                course_advice.confirm_advice(item['id'], course_advice.Revision(revision=2))
            item = course_advice.review_advice(item['id'], course_advice.Revision(revision=2))
            item = course_advice.confirm_advice(item['id'], course_advice.Revision(revision=2))
        self.assertEqual(item['state'], 'confirmed')

    def test_agent_reads_only_selected_student_and_course(self):
        raw = '学号,姓名,成绩,满分\n01,小明,3.5,5\n02,小红,4,5\n'.encode()
        mapping = course_data.parse('scores.csv', raw)['mapping']
        invoke(course_data.import_file(self.course['id'], '一班', json.dumps(mapping), hashlib.sha256(raw).hexdigest(), upload(raw)))
        student = next(s['id'] for s in course_data.course_profile(self.course['id'])['students'] if s['name'] == '小明')
        replies = [{'tool_calls':[{'id':'call1','function':{'name':'read_course_records','arguments':'{"class_name":"","activity":""}'}}]},
                   {'content':'小明本次得分3.5分。'}]
        with patch('backend.course_agent.complete_with_tools', side_effect=replies) as model, patch('backend.course_agent.model_name', return_value='test'):
            result = course_agent.run_course_agent(ChatRequest(question='分析成绩', course_id=self.course['id'], course_student_id=student), 'test')
        self.assertEqual(result['trace'][0]['count'], 1)
        payload = json.loads(model.call_args_list[1].args[1][-1]['content'])
        self.assertEqual(len(payload['records']), 1)
        self.assertEqual(payload['records'][0]['student_name'], '小明')
