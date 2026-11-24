from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from backend.cleaning_agent import CleaningReview, structured_review
from backend.model_transport import ModelRequestError, ModelTransport, response_code
from backend.recommendation_review import FinalReview, representative_events, review_in_groups


class ModelTransportTests(unittest.TestCase):
    def call(self, results):
        sent = []

        def respond(request):
            sent.append(request)
            result = results[len(sent) - 1]
            if isinstance(result, type):
                raise result("synthetic", request=request)
            return result

        transport = ModelTransport("test", "test", "case-id", httpx.MockTransport(respond))
        with patch("backend.model_transport.time.sleep") as sleep, httpx.Client(transport=transport) as client:
            try:
                response = client.post("https://test.invalid/chat/completions", json={"messages": []})
                code = response_code(response)
            except ModelRequestError as error:
                code = error.code
        return code, len(sent), sleep

    def test_recovers_after_connection_error(self):
        code, calls, _ = self.call([httpx.ConnectError, httpx.Response(200)])
        self.assertEqual((code, calls), ("", 2))

    def test_read_timeout_retries_once(self):
        code, calls, _ = self.call([httpx.ReadTimeout, httpx.ReadTimeout])
        self.assertEqual((code, calls), ("timeout", 2))

    def test_honors_retry_after_and_execution_budget(self):
        code, calls, sleep = self.call([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200)])
        self.assertEqual((code, calls), ("", 2))
        sleep.assert_called_once_with(3)
        code, calls, sleep = self.call([httpx.Response(429, headers={"retry-after": "60"})])
        self.assertEqual((code, calls), ("rate_limit", 1))
        sleep.assert_not_called()

    def test_credentials_and_context_are_not_retried(self):
        for response, expected in [(httpx.Response(401), "credentials"),
                                   (httpx.Response(400, json={"error": {"code": "context_length_exceeded"}}), "context")]:
            with self.subTest(expected=expected):
                code, calls, _ = self.call([response])
                self.assertEqual((code, calls), (expected, 1))
        with patch("backend.model_transport.request_budget", return_value=1):
            code, calls, _ = self.call([])
        self.assertEqual((code, calls), ("context", 0))

    def test_structured_responses_distinguish_empty_invalid_json_and_invalid_schema(self):
        for content, expected in [("", "empty"), ("{oops}", "json"), ("{}", "schema")]:
            with self.subTest(expected=expected):
                model = SimpleNamespace(generate=lambda _: SimpleNamespace(content=content))
                with patch("backend.cleaning_agent.configured_model", return_value=model):
                    with self.assertRaises(ModelRequestError) as failed:
                        structured_review(CleaningReview, "", {}, "test")
                self.assertEqual(failed.exception.code, expected)

    def test_representative_samples_keep_anchor_and_cover_score_bands(self):
        rows = {str(index): {"得分": score, "满分": 10, "节点编号": node}
                for index, (score, node) in enumerate([(0, "N01"), (0, "N01"), (0, "N02"),
                                                       (5, "N03"), (9, "N04")])}
        self.assertEqual(representative_events(list(rows), rows, "0"), ["0", "2", "3", "4"])

    def test_split_review_requires_all_checks_and_keeps_linked_evidence(self):
        checks = dict.fromkeys(("sample", "student_text", "source", "exercise"), "check")
        facts = {"checks": checks, "observation": "", "uncertainty": "", "insufficient_materials": False,
                 "axes": [], "evidence": {"attempt": {}}, "teaching_points": [{"citation_ids": ["ref"]}],
                 "citations": {"ref": {"text": "source"}}, "student_text": "", "variant": None,
                 "exercises": [{"question_id": "Q1", "evidence_ids": ["attempt"]}], "questions": {"Q1": {}}}

        def review(_, __, group, ___):
            key, = group["checks"]
            if key == "source":
                self.assertEqual(group["citations"]["ref"]["text"], "source")
                return FinalReview(decision="revise", checks={key: "revise"}, summary="revise",
                                   findings=[{"detail": "recover reference", "refs": ["ref"]}]), "test"
            if key == "exercise":
                self.assertIn("attempt", group["evidence"])
                self.assertIn("Q1", group["questions"])
            return FinalReview(decision="pass", checks={key: "pass"}, summary="ok", findings=[]), "test"

        with patch("backend.recommendation_review.structured_review", side_effect=review):
            result, _ = review_in_groups(facts, "check", "test")
        self.assertEqual(set(result.checks), set(checks))
        self.assertEqual(result.decision, "revise")
