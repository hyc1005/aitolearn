from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import patch

from backend.agent import Draft, generate
from backend.model_transport import ModelRequestError
from backend.recommendation_review import FinalReview, review_checks


class GroundedDraftTests(TestCase):
    def setUp(self):
        self.axes = [{"id":"I1", "label":"分数运算", "score":0.5, "n":2, "coverage":2, "attempt_ids":["E1", "E2"]}]
        self.enterContext(patch('backend.agent.profile', return_value={"axes":self.axes, "size":2}))
        self.tables = {
            "作答记录": {key:{"指标":["I1"], "材料编号":"Q1", "节点编号":"N1", "得分":"1", "满分":"2"}
                        for key in ("E1", "E2", "OUTSIDE")},
            "测验节点":{"N1":{"可用题目编号":"Q1、Q2"}, "N2":{"可用题目编号":"Q3"}},
            "题目与材料":{key:{"指标编号":"I1", "题干或内容":"示例", "来源或资料文件":"reference.pdf"}
                        for key in ("Q1", "Q2", "Q3")},
        }
        self.payload = {"variant":True, "node":"N1", "anchor_event":"E1", "material_ids":[]}
        self.interpretation = {"summary":"本次观察到分数运算仍需复习。", "claims":[{"detail":"观察", "refs":["I1", "E2", "OUTSIDE"]}], "model":"test"}

    def test_no_material_observation_never_searches_or_calls_model(self):
        with patch('backend.agent.search') as search, patch('backend.agent.write_grounded_draft') as write:
            draft = generate(self.tables, self.payload, 'run', self.interpretation)
        search.assert_not_called()
        write.assert_not_called()
        self.assertEqual(draft['observation'], self.interpretation['summary'])
        self.assertEqual(draft['draft_mode'], 'observation')
        self.assertEqual(draft['material_gap'], 'no_published_material')
        self.assertEqual(draft['evidence_ids'], ['E1', 'E2'])
        self.assertEqual(draft['citations'], [])
        self.assertEqual(draft['exercises'], [])
        self.assertIsNone(draft['checked_variant'])
        self.assertIn('未生成变式题', draft['variant_omission_reason'])

    def test_pinned_versions_without_a_match_yield_observation(self):
        with patch('backend.agent.db', return_value=nullcontext(None)), patch('backend.agent.search', return_value=[]):
            draft = generate(self.tables, {**self.payload, 'material_ids':['known']}, 'run', self.interpretation)
        self.assertEqual(draft['material_gap'], 'no_relevant_material')
        self.assertEqual([action['tool'] for action in draft['trace']], ['fixed_evidence', 'search_materials'])

    def test_final_review_recovers_from_output_truncation(self):
        facts = {'checks': {'sample': '依据'}, 'allowed_refs': ['sample']}
        result = FinalReview(decision='pass', checks={'sample':'pass'}, summary='通过', findings=[])
        with patch('backend.recommendation_review.structured_review',
                   side_effect=[ModelRequestError('truncated'), (result, 'stub')]) as model:
            review, _ = review_checks(facts, '核对', 'run')
        self.assertEqual(review.decision, 'pass')
        self.assertEqual([call.kwargs['max_tokens'] for call in model.call_args_list], [6000, 10000])

    def test_write_retry_uses_same_evidence_and_no_new_search(self):
        result = Draft(observation='建议复习', evidence_ids=['E1'], citation_ids=['C1'],
                       teaching_points=[{'text':'先通分', 'citation_ids':['C1']}], uncertainty='样本有限',
                       exercises=[{'question_id':'Q2','purpose':'练习通分','hints':['先看分母'],'evidence_ids':['E1']}],
                       follow_up='先核对分母。')
        incorrect = result.model_copy(update={'evidence_ids':['invented']})
        for failure, expected in [(incorrect, [8000, 8000]), (ModelRequestError('truncated'), [8000, 12000])]:
            with self.subTest(expected=expected):
                first = (failure, 'stub') if isinstance(failure, Draft) else failure
                with patch('backend.agent.db', return_value=nullcontext(None)), patch('backend.agent.search', return_value=[
                    {'id':'C1','material':'known','text':'通分例题','locator':'第 12 页'},
                    {'id':'C2','material':'unbound','text':'不属于该版本','locator':'第 1 页'},
                ]) as search, patch('backend.cleaning_agent.structured_review', side_effect=[first, (result, 'stub')]) as model:
                    draft = generate(self.tables, {**self.payload, 'material_ids':['known']}, 'run', self.interpretation)
                search.assert_called_once_with(None, '分数运算', limit=2, material_ids=['known'])
                a, b = model.call_args_list
                self.assertIs(a.args[2], b.args[2])
                self.assertEqual([a.kwargs['max_tokens'], b.kwargs['max_tokens']], expected)
                self.assertEqual(set(b.args[2]['citations']), {'C1'})
                self.assertNotIn('Q3', b.args[2]['candidate_questions'])
                self.assertEqual(draft['draft_mode'], 'full')
                self.assertEqual([c['id'] for c in draft['citations']], ['C1'])
                self.assertEqual(list(draft['questions']), ['Q2'])
