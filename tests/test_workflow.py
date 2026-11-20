import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store, workflow
from backend.agent import Draft, Variant, validate_links, validate_variant
from backend.cleaning_agent import CleaningReview, review_import
from backend.data import read_file
from backend.main import app
from backend.recommendation_review import FinalReview, final_review
from backend.snapshots import run_data_cleaning, table_data


FIXTURE = next(Path("ori_data").rglob("*.xlsx"), None)


class CaseReplayTests(TestCase):
    def test_missing_revisions_keep_recorded_review_and_do_not_write(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(store, "ROOT", Path(directory)))
        draft_id = "2809aa96e95e4568a990e4f10828b44a"
        with store.db() as conn:
            store.put(conn, "job", draft_id, {
                "id": draft_id,
                "result": {"draft": draft_id, "revision": 1},
                "stages": {"final_review": {"input_version": 1, "decision": "revise"}},
            })
            before = [tuple(row) for row in conn.execute("SELECT * FROM records")]

        with TestClient(app) as client:
            response = client.get("/api/agent/cases/q015")
        self.assertEqual(response.status_code, 200)
        steps = response.json()["steps"]
        self.assertIsNone(steps[0]["evidence"])
        self.assertIsNone(steps[1]["before"])
        self.assertEqual(steps[2]["result"]["review"]["decision"], "revise")
        self.assertIsNone(steps[3]["after"])
        self.assertIsNone(steps[4]["result"])
        with store.db() as conn:
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM records")], before)


@skipUnless(FIXTURE, "赛题资料未复制到 ori_data")
class WorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.rows = read_file(FIXTURE.name, cls.raw)

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(store, "ROOT", Path(directory)))
        self.client = self.enterContext(TestClient(app))

    def post_import(self):
        response = self.client.post("/api/imports", files={"files": (FIXTURE.name, self.raw)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_cleaning(self, key, check_model=True):
        with store.db() as conn:
            item = store.get(conn, "job", key)
        if check_model:
            with patch.object(workflow, "review_import", return_value={"decision": "ready", "model": "test"}):
                workflow.run_cleaning(item)
        else:
            workflow.run_cleaning(item)
        with store.db() as conn:
            store.put(conn, "job", key, {**item, "state": "done"})
        return item

    def confirm(self, batch):
        return self.client.post(f"/api/imports/{batch['batch_id']}/confirm", params={"run_id": batch["run_id"]})

    def test_determinism_and_duplicates(self):
        cleaned = run_data_cleaning(self.rows)
        self.assertEqual(cleaned, run_data_cleaning(reversed(self.rows)))
        repeated = run_data_cleaning(self.rows * 2)
        self.assertEqual(table_data(cleaned), table_data(repeated))
        self.assertEqual(repeated["report"]["duplicate_attempts"], 806)
        self.assertEqual(repeated["report"]["valid_scored"], 785)

    def test_new_student_identifiers_do_not_change_valid_scores(self):
        rows = []
        for row in reversed(self.rows):
            values = row["values"]
            if values.get("学生编号") == "D01":
                values = {**values, "学生编号": "NEW01"}
                if row["table"] == "作答记录":
                    values["作答事件编号"] = values["作答事件编号"].replace("D01-", "NEW01-", 1)
            rows.append({**row, "values": values})
        cleaned = run_data_cleaning(rows)
        self.assertEqual(cleaned["report"]["valid_scored"], 785)
        record = cleaned["tables"]["作答记录"]["NEW01-N01-Q015"]
        self.assertEqual((record["学生编号"], record["得分"], record["满分"]), ("NEW01", "2", "3"))

    def test_confirmation_pins_all_reads_and_detects_stale_baseline(self):
        first, competing = self.post_import(), self.post_import()
        self.assertEqual(self.confirm(first).status_code, 409)
        self.run_cleaning(first["run_id"])
        self.run_cleaning(competing["run_id"])
        confirmed = self.confirm(first)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.confirm(first).json(), confirmed.json())
        self.assertEqual(self.confirm(competing).status_code, 409)
        rebased = self.client.post(f"/api/imports/{competing['batch_id']}/rebase").json()
        self.assertNotEqual(rebased["run_id"], competing["run_id"])
        snapshot_id = confirmed.json()["snapshot_id"]
        with patch("backend.snapshots.clean", side_effect=AssertionError("unexpected recomputation")):
            for endpoint in ("/api/profiles", "/api/export", "/api/imports/report"):
                self.assertEqual(self.client.get(endpoint).json()["snapshot_id"], snapshot_id)
            for student in ("D01", "D14", "D17"):
                data = self.client.get("/api/profiles", params={"student": student, "node": "N01"}).json()
                self.assertEqual(data["profile"]["student"], student)
            for group in ("DC01", "DC02"):
                data = self.client.get("/api/profiles", params={"group": group, "node": "N02"}).json()
                self.assertEqual(data["profile"]["group"], group)
            early = self.client.get("/api/profiles", params={"student": "D01", "node": "N01"}).json()
            later = self.client.get("/api/profiles", params={"student": "D01", "node": "N02"}).json()
            self.assertTrue(any(axis["status"] == "未教学" for axis in early["profile"]["axes"]))
            self.assertNotEqual(early["profile"]["axes"], later["profile"]["axes"])
            rows = self.client.get("/api/profiles/evidence", params={"snapshot_id": snapshot_id,
                                   "student": "D01", "node": "N01", "indicator": "T01I2"}).json()
            question = next(r for r in rows if r["材料编号"] == "Q015")
            self.assertEqual((question["得分"], question["满分"]), ("2", "3"))

    def test_recommendation_pins_versions_and_checks_anchor_scope(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        key = self.confirm(batch).json()["snapshot_id"]
        with store.db() as conn:
            store.put(conn, "material", "published", {"id": "published", "state": "published"})
            store.put(conn, "material", "draft", {"id": "draft", "state": "review"})
        request = {"student": "D01", "node": "N01", "snapshot_id": key, "anchor_event": "D01-N01-Q015"}
        with patch("backend.main.api_key", return_value="configured"):
            result = self.client.post("/api/agent/runs", json=request)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["payload"]["material_ids"], ["published"])
            self.assertEqual(result.json()["payload"]["snapshot_id"], key)
            self.assertEqual(self.client.post("/api/agent/runs", json={**request, "student": "D14"}).status_code, 400)
            self.assertEqual(self.client.post("/api/agent/runs", json={**request, "node": "N02"}).status_code, 400)
            next_batch = self.post_import()
            self.run_cleaning(next_batch["run_id"])
            newer = self.confirm(next_batch).json()["snapshot_id"]
            with store.db() as conn:
                store.put(conn, "material", "published", {"id": "published", "state": "replaced"})
                store.put(conn, "material", "draft", {"id": "draft", "state": "published"})
            self.assertNotEqual(newer, key)
            historical = self.client.get(f"/api/agent/runs/{result.json()['run_id']}").json()
            self.assertEqual(historical["payload"]["snapshot_id"], key)
            self.assertEqual(historical["payload"]["material_ids"], ["published"])
            self.assertEqual(self.client.post("/api/agent/runs", json=request).status_code, 409)

    def test_invalid_variant_answer_cannot_pass(self):
        exercise = Variant(parent_id="Q015", kind="linear_equation", a=2, b=1, c=7, answer="1")
        with self.assertRaisesRegex(ValueError, "答案校验失败"):
            validate_variant(exercise)

    def test_teaching_and_exercise_links_are_checked_individually(self):
        draft = Draft.model_validate({
            "observation": "合并同类项", "evidence_ids": ["attempt", "unrelated"],
            "citation_ids": ["paragraph"], "teaching_points": [{"text": "先核对符号", "citation_ids": ["paragraph"]}],
            "uncertainty": "样本有限", "follow_up": "写出中间步骤",
            "exercises": [{"question_id": "Q015", "purpose": "核对符号", "hints": ["圈出负号"],
                           "evidence_ids": ["attempt"]}],
        })
        attempts = {"attempt": {"指标": ["T01I2"]}, "unrelated": {"指标": ["T02I1"]}}
        questions = {"Q015": {"指标编号": "T01I2"}}
        validate_links(draft, attempts, questions)
        draft.exercises[0].evidence_ids = ["unrelated"]
        with self.assertRaisesRegex(ValueError, "练习目的"):
            validate_links(draft, attempts, questions)
        draft.teaching_points[0].citation_ids = ["wrong"]
        with self.assertRaisesRegex(ValueError, "教学说明"):
            validate_links(draft, attempts, questions)

    def test_retry_reuses_python_result(self):
        batch = self.post_import()
        with patch.object(workflow, "review_import", side_effect=ValueError("model timeout")):
            with self.assertRaisesRegex(ValueError, "model timeout"):
                self.run_cleaning(batch["run_id"], check_model=False)
        details = self.client.get(f"/api/agent/runs/{batch['run_id']}").json()
        self.assertEqual(details["report"]["valid_scored"], 785)
        self.assertEqual(self.confirm(batch).status_code, 409)
        with patch.object(workflow, "run_data_cleaning", side_effect=AssertionError("reran Python")):
            self.run_cleaning(batch["run_id"])
        self.assertEqual(self.confirm(batch).status_code, 200)

    def test_legacy_batches_require_migration_confirmation(self):
        with store.db() as conn:
            store.put(conn, "import", "old", {"id": "old", "confirmed": True, "rows": self.rows, "sources": []})
        self.assertTrue(self.client.get("/api/imports/report").json()["legacy_pending"])
        batch = self.client.post("/api/imports/migrate").json()
        self.assertEqual(self.client.post("/api/imports/migrate").json(), batch)
        self.run_cleaning(batch["run_id"])
        self.assertEqual(self.confirm(batch).status_code, 200)
        self.assertEqual(self.client.get("/api/imports/report").json()["valid_scored"], 785)

    def test_teacher_correction_is_only_visible_after_confirmation(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        first = self.confirm(batch).json()["snapshot_id"]
        report = self.client.get("/api/imports/report").json()
        issue = next(row for row in report["issues"] if row["line"] == 362)
        values = next(row["values"] for row in self.rows if row["sheet"] == issue["sheet"] and row["line"] == issue["line"])
        correction = {field: issue[field] for field in ("source", "sheet", "line", "file_id")}
        correction["changes"] = {"评分状态": "待评分", "得分": "", "评分点得分": ""}
        response = self.client.post("/api/imports/corrections", json=correction)
        self.assertEqual(response.status_code, 200, response.text)
        corrected = response.json()
        self.run_cleaning(corrected["run_id"])
        self.assertEqual(self.client.get("/api/imports/report").json()["snapshot_id"], first)
        new = self.confirm(corrected).json()["snapshot_id"]
        self.assertNotEqual(first, new)
        with store.db() as conn:
            previous = store.get(conn, "snapshot", first)
            current = store.get(conn, "snapshot", new)
        self.assertNotIn(values["作答事件编号"], previous["tables"]["作答记录"])
        self.assertEqual(current["tables"]["作答记录"][values["作答事件编号"]]["评分状态"], "待评分")
        self.assertEqual(current["manifest"][-1]["revision_of"]["line"], issue["line"])

    def test_cleaning_review_rejects_wrong_counts(self):
        def wrong_counts(schema, prompt, facts, session):
            return CleaningReview(counts={**facts["counts"], "valid_scored": 790},
                                  summary="计数复核", findings=[], decision="ready"), "test"
        with patch("backend.cleaning_agent.structured_review", side_effect=wrong_counts):
            with self.assertRaisesRegex(ValueError, "不一致"):
                review_import(run_data_cleaning(self.rows), None, "test")

    def test_draft_revision_requires_matching_final_review(self):
        draft = {"id": "draft", "student": "D01", "group": "", "revision": 1, "snapshot_id": "snapshot",
                 "message": "旧正文", "exercises": [], "approved": False}
        with store.db() as conn:
            store.put(conn, "draft", draft["id"], draft)
        with patch.object(workflow, "final_review", return_value={"decision": "pass", "input_version": 2}):
            endpoint = "/api/recommendations/draft"
            edit = {"revision": 1, "exercises": [], "message": "修订正文", "approved": True}
            saved = self.client.put(endpoint, json=edit).json()
            self.assertFalse(saved["approved"])
            edit["revision"] = 2
            self.assertEqual(self.client.put(endpoint, json=edit).status_code, 409)
            with store.db() as conn:
                item = store.get(conn, "job", saved["review_run_id"])
            workflow.review_recommendation(item, draft["id"], 2)
            with store.db() as conn:
                store.put(conn, "job", item["id"], {**item, "state": "done"})
            self.assertTrue(self.client.put(endpoint, json=edit).json()["approved"])

    def test_final_review_rejects_fabricated_reference(self):
        draft = {"revision": 1, "observation": "", "uncertainty": "", "evidence": [], "citations": [],
                 "profile": {"axes": []}, "exercises": [], "questions": {}, "checked_variant": None,
                 "insufficient_materials": True}
        result = FinalReview(decision="revise", checks={"sample": "pass", "source": "revise", "exercise": "pass",
                             "student_text": "pass"}, summary="退回", findings=[{"detail": "缺少引用", "refs": ["made-up"]}])
        with patch("backend.recommendation_review.structured_review", return_value=(result, "test")):
            with self.assertRaisesRegex(ValueError, "无效引用"):
                final_review(draft, "test")
