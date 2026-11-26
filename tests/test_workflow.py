import json
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import store, workflow
from backend.agent import Draft, Variant, validate_links, validate_variant
from backend.chat_agent import ChatTools, run_chat
from backend.cleaning_agent import CleaningReview, review_import
from backend.data import read_file
from backend.main import app
from backend.learning_chat import ChatRequest
from backend.model_transport import ModelRequestError
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

    def test_import_bundle_skips_field_dictionary_without_partial_upload(self):
        paths = list(next(Path("ori_data").rglob("字段说明.csv")).parent.glob("*.csv"))
        files = [("files", (path.name, path.read_bytes(), "text/csv")) for path in paths]
        response = self.client.post("/api/imports", files=files)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["skipped"], ["字段说明.csv"])
        with store.db() as conn:
            batch = store.get(conn, "import", response.json()["batch_id"])
            counts = {name: sum(row["table"] == name for row in batch["rows"])
                      for name in ("模拟学生", "课程要求", "测验节点", "题目与材料", "作答记录")}
        self.assertEqual(counts, {"模拟学生": 40, "课程要求": 9, "测验节点": 2, "题目与材料": 65, "作答记录": 806})

    def test_import_preview_identifies_files_and_missing_columns(self):
        folder = next(Path("ori_data").rglob("字段说明.csv")).parent
        files = [("files", (path.name, path.read_bytes(), "text/csv")) for path in folder.glob("*.csv")]
        result = self.client.post("/api/imports/preview", files=files).json()
        self.assertTrue(result["can_import"])
        self.assertEqual(next(item for item in result["files"] if item["name"] == "字段说明.csv")["status"], "skipped")
        missing = self.client.post("/api/imports/preview", files={
            "files": ("模拟学生.csv", "学生编号\nD01\n".encode(), "text/csv")}).json()
        self.assertFalse(missing["can_import"])
        self.assertIn("模拟班级编号", missing["files"][0]["message"])
        self.assertFalse((store.ROOT / "files").exists())

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

    def test_follow_up_reads_only_confirmed_later_assessments(self):
        def upload(rows, name):
            payload = "".join(rows).encode()
            response = self.client.post("/api/imports", files={"files": (name, payload, "text/csv")})
            self.assertEqual(response.status_code, 200, response.text)
            batch = response.json()
            self.run_cleaning(batch["run_id"])
            return batch

        base_header = "记录编号,学生编号,材料编号,节点编号,作答事件编号,修订版本,评分状态,得分,满分,主题编号\n"
        sources = {
            "模拟学生.csv": "学生编号,模拟班级编号\nD01,A\nD02,A\n",
            "课程要求.csv": "指标编号,主题编号,观察指标\nI1,T1,去括号\n",
            "测验节点.csv": "节点编号,可用题目编号,模拟测验日期\nN01,Q1,2026-01-20\n",
            "题目与材料.csv": ("材料编号,材料类型,主题编号,题型,指标编号,满分\n"
                              "Q1,测验题,T1,解答题,I1,4\nQ2,测验题,T1,解答题,I1,8\n"
                              "Q3,测验题,T2,解答题,I1,8\n"),
            "作答记录.csv": base_header + "A1,D01,Q1,N01,E1,1,已评分,0,4,T1\n",
        }
        response = self.client.post("/api/imports", files=[
            ("files", (name, source.encode(), "text/csv")) for name, source in sources.items()
        ])
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        run = self.run_cleaning(first["run_id"])
        self.assertEqual(self.confirm(first).status_code, 200)
        draft = {"id": "example", "student": "D01", "exercises": [{"question_id": "Q1"}], "revision": 1,
                 "snapshot_id": run["result"]["snapshot_id"], "approved": False, "node": "N01"}
        path = "/api/recommendations/example/follow-up"
        directions = ["indicator:I1", "form:解答题"]
        assignment = {"revision": 1, "baseline_node": "N01", "directions": directions}
        with store.db() as conn:
            store.put(conn, "draft", "example", draft)
            store.put(conn, "draft_revision", "example:1", draft)
        self.assertEqual(self.client.get(path, params={"revision": 1}).json()["default_node"], "N01")
        self.assertEqual(self.client.put(path, json=assignment).status_code, 409)
        with store.db() as conn:
            store.put(conn, "draft", "example", {**draft, "approved": True})
        self.assertEqual(self.client.put(path, json={**assignment, "directions": ["unknown"]}).status_code, 422)
        recorded = self.client.put(path, json=assignment)
        self.assertEqual(recorded.status_code, 200, recorded.text)
        self.assertEqual(self.client.put(path, json=assignment).json()["assigned_at"], recorded.json()["assigned_at"])
        new_nodes = upload(["节点编号,可用题目编号,模拟测验日期\n",
                            'N02,"Q2、Q3",2026-06-17\n'], "测验节点.csv")
        self.assertEqual(self.confirm(new_nodes).status_code, 200)
        second = upload([base_header, "A1R,D01,Q1,N01,E1,2,已评分,4,4,T1\n",
                         "A2,D01,Q2,N02,E2,1,已评分,6,8,T1\n",
                         "A3,D01,Q3,N02,E3,1,已评分,8,8,T2\n",
                         "A4,D02,Q2,N02,E4,1,已评分,8,8,T1\n"], "作答记录.csv")
        pending = self.client.get(path, params={"revision": 1}).json()["assignment"]["results"]
        self.assertEqual(pending[0]["observations"][0]["status"], "missing")
        self.assertEqual(self.confirm(second).status_code, 200)
        items = self.client.get("/api/students/D01/follow-ups").json()
        indicator, form = items[0]["results"]
        self.assertEqual(indicator["baseline"]["rate"], 0)
        self.assertEqual([value["node"] for value in form["observations"]], ["N02"])
        self.assertEqual((indicator["observations"][0]["count"], indicator["observations"][0]["rate"]), (2, 87.5))
        self.assertEqual((form["observations"][0]["count"], form["observations"][0]["rate"]), (1, 75))
        self.assertEqual(form["observations"][0]["attempts"][0]["作答事件编号"], "E2")

    def test_stop_preserves_completed_cleaning_checkpoint(self):
        batch = self.post_import()
        key = batch["run_id"]
        self.assertEqual(self.client.post(f"/api/jobs/{key}/control/stop").json()["state"], "stopped")
        self.assertEqual(self.client.post(f"/api/jobs/{key}/retry").json()["state"], "queued")
        with store.db() as conn:
            job = store.get(conn, "job", key)
            job["state"] = "running"
            store.put(conn, "job", key, job)
        self.assertTrue(self.client.post(f"/api/jobs/{key}/control/stop").json()["stop_requested"])
        workflow.checkpoint(job, "python", {"snapshot_id": "example"})
        with self.assertRaises(workflow.JobStopped):
            workflow.checkpoint(job, "clean_review")
        with store.db() as conn:
            self.assertIn("python", store.get(conn, "job", key)["stages"])

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

    def test_chat_accepts_only_local_quotes_and_keeps_the_selected_student(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        snapshot = self.confirm(batch).json()["snapshot_id"]
        with store.db() as conn:
            store.put(conn, "material", "published-chat", {"id": "published-chat", "state": "published",
                "blocks": [{"id": "q015-rule", "material": "published-chat", "title": "整式教材",
                            "locator": "Q015", "text": "Q015 去括号时，括号前是负号需要变号。"}]})
            store.put(conn, "material", "draft-chat", {"id": "draft-chat", "state": "review",
                "blocks": [{"id": "draft-block", "material": "draft-chat", "title": "待审",
                            "locator": "Q015", "text": "Q015 待审内容"}]})

        def selected_quotes(provider, messages, session, **options):
            payload = json.loads(messages[-1]["content"])
            evidence = payload["sources"]
            self.assertFalse(any("待审内容" in item["text"] for item in evidence))
            student = next(item for item in evidence if item["kind"] == "answer")
            self.assertNotIn("D01", student["text"])
            material = next(item for item in evidence if "括号前是负号" in item["text"])
            return json.dumps({"answer": "这次作答得到 2 分，满分 3 分。",
                               "quotes": [{"id": student["id"], "quote": "得分 2/3"},
                                          {"id": material["id"], "quote": "括号前是负号需要变号"},
                                          {"id": student["id"], "quote": "编造的成绩"},
                                          {"id": 999, "quote": "未登记的教材"}]})

        body = {"snapshot_id": snapshot, "student": "D01", "node": "N01", "question": "Q015 的失分和去括号依据"}
        with patch("backend.learning_chat.complete", side_effect=selected_quotes):
            response = self.client.post("/api/learning-chat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "这次作答得到 2 分，满分 3 分。")
        parts = response.json()["parts"]
        self.assertEqual([part["quote"] for part in parts], ["得分 2/3", "括号前是负号需要变号"])
        self.assertEqual(parts[0]["source"]["attempt_id"], "D01-N01-Q015")
        self.assertEqual(parts[1]["source"]["citation"]["id"], "q015-rule")
        self.assertEqual(self.client.post("/api/learning-chat", json={**body, "snapshot_id": "missing"}).status_code, 404)
        self.assertEqual(self.client.post("/api/learning-chat", json={**body, "student": "missing"}).status_code, 422)
        with patch("backend.learning_chat.complete", return_value='{"answer":"得了满分","quotes":[]}'):
            missing = self.client.post("/api/learning-chat", json={**body, "question": "Q999 的得分是多少？"})
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertIn("作答证据不足", missing.json()["answer"])

    def test_general_chat_works_without_a_snapshot_or_sources(self):
        def answer(provider, messages, session, **options):
            payload = json.loads(messages[-1]["content"])
            self.assertEqual(payload["sources"], [])
            self.assertEqual(payload["history"], [{"question": "什么是彩虹？", "answer": "一种光学现象。"}])
            return json.dumps({"answer": "雨滴折射、反射并分散阳光。", "quotes": []})

        with patch("backend.learning_chat.complete", side_effect=answer):
            response = self.client.post("/api/learning-chat", json={
                "question": "具体是怎么形成的？",
                "history": [{"question": "什么是彩虹？", "answer": "一种光学现象。"}],
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "雨滴折射、反射并分散阳光。")
        self.assertEqual(response.json()["parts"], [])

    def test_model_access_activates_provider_without_exposing_key(self):
        credentials = {}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}), \
                patch("backend.models.keyring.get_password", side_effect=lambda service, provider: credentials.get(provider)), \
                patch("backend.main.keyring.set_password", side_effect=lambda service, provider, key: credentials.update({provider: key})):
            path = "/api/models/settings/deepseek"
            self.assertEqual(self.client.put(path, json={}).status_code, 422)
            response = self.client.put(path, json={"api_key": "synthetic-test-key"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["active"], "deepseek")
            self.assertEqual(credentials["deepseek"], "synthetic-test-key")
            self.assertNotIn("synthetic-test-key", response.text)
            with patch("backend.main.complete", return_value="OK") as probe:
                self.assertEqual(self.client.post("/api/models/test/deepseek").status_code, 200)
                self.assertEqual(probe.call_args.args[0], "deepseek")

    def test_distribution_matches_pinned_evidence_and_profile(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        snapshot = self.confirm(batch).json()["snapshot_id"]
        common = {"group": "DC02", "node": "N01", "snapshot_id": snapshot}
        profiles = self.client.get("/api/profiles", params=common).json()
        self.assertEqual(profiles["snapshot_id"], snapshot)
        axis = next(axis for axis in profiles["profile"]["axes"] if axis["id"] == "T01I2")
        target = {**common, "indicator": axis["id"]}
        result = self.client.get("/api/profiles/distribution", params=target).json()
        self.assertEqual({key: result[key] for key in target}, target)
        self.assertEqual(sorted(row["score"] for row in result["students"]), axis["distribution"])
        self.assertEqual(sum(row["n"] for row in result["students"]), axis["n"])
        for point in result["students"]:
            rows = self.client.get("/api/profiles/evidence", params={**target, "student": point["student"]}).json()
            self.assertEqual(len(rows), point["n"])
            rate = 100 * sum(int(row["得分"]) for row in rows) / sum(int(row["满分"]) for row in rows)
            self.assertEqual(point["score"], round(rate, 1))
        self.assertEqual(self.client.get("/api/profiles", params={**common, "snapshot_id": "missing"}).status_code, 404)
        self.assertEqual(self.client.get("/api/profiles/distribution", params={**target, "snapshot_id": "missing"}).status_code, 404)

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

    def test_cleaning_review_keeps_python_counts_and_repairs_invalid_reference(self):
        answers = [
            CleaningReview(summary="检查", findings=[{"detail": "核对", "refs": ["wrong"]}], decision="ready"),
            CleaningReview(summary="检查", findings=[{"detail": "核对", "refs": ["counts"]}], decision="ready"),
        ]
        snapshot = run_data_cleaning(self.rows)
        with patch("backend.cleaning_agent.structured_review", side_effect=[(value, "test") for value in answers]) as review:
            result = review_import(snapshot, None, "test")
        self.assertEqual(review.call_count, 2)
        self.assertEqual(result["counts"]["valid_scored"], snapshot["report"]["valid_scored"])

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

    def test_agent_approval_and_export_require_current_versions(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        sid = self.confirm(batch).json()["snapshot_id"]
        draft = {"id": "draft", "student": "D01", "group": "", "node": "", "revision": 1,
                 "snapshot_id": sid, "message": "复习去括号", "exercises": [], "approved": False,
                 "review_state": "pass", "review_run_id": "review",
                 "final_review": {"decision": "pass", "input_version": 1}, "material_ids": []}
        with store.db() as conn:
            store.put(conn, "draft", "draft", draft)
            store.put(conn, "job", "review", {"id": "review", "state": "done"})
            snapshot = store.get(conn, "snapshot", sid)
        tools = ChatTools(ChatRequest(question="确认 D01 的建议"), snapshot)
        self.assertEqual(tools.resolve_scope()["status"], "ok")
        self.assertEqual(tools.propose_approve_draft("draft")["status"], "not_ready")
        tools.read_drafts()
        self.assertEqual(tools.propose_approve_draft("draft")["status"], "waiting")
        proposal = tools.cards[-1]
        with store.db() as conn:
            store.put(conn, "draft", "draft", {**draft, "message": "已经修改"})
        path = '/api/chat/proposals/' + proposal['id'] + '/confirm'
        self.assertEqual(self.client.post(path).status_code, 409)
        with store.db() as conn:
            store.put(conn, "draft", "draft", draft)
        result = self.client.post(path)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["result"]["approved"])
        self.assertEqual(self.client.post(path).json()["result"], result.json()["result"])
        export = ChatTools(ChatRequest(question="下载正式成果"), snapshot)
        self.assertEqual(export.prepare_formal_export()["count"], 1)
        self.assertEqual(self.client.get('/api/export', params={'formal': 'true', 'expected_snapshot': 'old'}).status_code, 409)
        self.assertEqual(self.client.get('/api/export', params={'formal': 'true', 'expected_snapshot': sid}).status_code, 200)

    def test_chat_retries_empty_model_response_before_tools(self):
        answer = {"role": "assistant", "content": '{"answer":"请先导入数据。","quotes":[]}'}
        with patch('backend.chat_agent.complete_with_tools', side_effect=[ModelRequestError('response'), answer]) as call:
            run_chat(ChatRequest(question="你好"), None, 'opencode')
        self.assertEqual(call.call_count, 2)

    def test_chat_retries_unparseable_final_answer(self):
        bad = {"role": "assistant", "content": '非 JSON 输出'}
        good = {"role": "assistant", "content": '{"answer":"请先导入数据。","quotes":[]}'}
        with patch('backend.chat_agent.complete_with_tools', side_effect=[bad, good]) as call:
            result = run_chat(ChatRequest(question="你好"), None, 'opencode')
        self.assertEqual(result['answer'], '请先导入数据。')
        self.assertEqual(call.call_count, 2)

    def test_agent_stop_and_resume_task_require_confirmation(self):
        job = {"id": "task", "kind": "clean", "state": "queued", "created": 1, "payload": {}}
        with store.db() as conn:
            store.put(conn, "job", job["id"], job)
        tools = ChatTools(ChatRequest(question="停止这个任务"), None)
        self.assertEqual(tools.propose_stop_job('task')['status'], 'missing')
        tools.read_jobs()
        self.assertEqual(tools.propose_stop_job('task')['status'], 'waiting')
        proposal = tools.cards[-1]
        with store.db() as conn:
            self.assertEqual(store.get(conn, 'job', 'task')['state'], 'queued')
        response = self.client.post('/api/chat/proposals/' + proposal['id'] + '/confirm')
        self.assertEqual(response.status_code, 200, response.text)
        with store.db() as conn:
            self.assertEqual(store.get(conn, 'job', 'task')['state'], 'stopped')
        retry = ChatTools(ChatRequest(question="重试这个任务"), None)
        retry.read_jobs()
        self.assertEqual(retry.propose_retry('task')['status'], 'waiting')
        response = self.client.post('/api/chat/proposals/' + retry.cards[-1]['id'] + '/confirm')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['result']['run_id'], 'task')

    def test_final_review_rejects_fabricated_reference(self):
        draft = {"revision": 1, "observation": "", "uncertainty": "", "evidence": [], "citations": [],
                 "profile": {"axes": []}, "exercises": [], "questions": {}, "checked_variant": None,
                 "insufficient_materials": True}
        result = FinalReview(decision="revise", checks={"sample": "pass", "source": "revise", "exercise": "pass",
                             "student_text": "pass"}, summary="退回", findings=[{"detail": "缺少引用", "refs": ["made-up"]}])
        with patch("backend.recommendation_review.structured_review", return_value=(result, "test")):
            with self.assertRaises(ModelRequestError) as failed:
                final_review(draft, "test")
        self.assertEqual(failed.exception.code, "reference")

    def test_chat_agent_executes_tools_and_preserves_the_evidence_scope(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        snapshot = self.confirm(batch).json()["snapshot_id"]
        observed = []

        def provider(_provider, messages, _session, **options):
            results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
            self.assertTrue(options["tools"])
            observed[:] = results
            steps = [("resolve_scope", {}), ("read_profile", {}), ("read_evidence", {})]
            if len(results) == len(steps):
                return {"role": "assistant", "content": '{"answer":"从覆盖率和分数一起判断。"}'}
            name, args = steps[len(results)]
            if name == "read_evidence":
                args["indicator"] = results[-1]["indicators"][0]["id"]
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{len(results)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }]}

        with patch("backend.main.provider_key", return_value="synthetic"), patch(
                "backend.chat_agent.complete_with_tools", side_effect=provider):
            response = self.client.post("/api/learning-chat", json={"use_tools": True,
                "question": "DC02 最近一次测验，哪个指标最值得先讲？给我看依据。", "snapshot_id": snapshot})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([step["tool"] for step in body["trace"]], ["resolve_scope", "read_profile", "read_evidence"])
        self.assertEqual(body["context"]["node"], "N02")
        self.assertEqual(observed[-1]["snapshot_id"], snapshot)
        self.assertEqual(observed[-1]["group"], "DC02")
        self.assertLessEqual(len(observed[-1]["examples"]), 3)
        session = self.client.get("/api/chat/sessions/" + body["session_id"]).json()
        self.assertEqual(session["turns"][0]["trace"], body["trace"])
        observed.clear()

        def propose(_provider, messages, _session, **_options):
            followup = len(messages) > 2
            if followup:
                scope = json.loads(messages[-1]["content"])
                self.assertEqual((scope["group"], scope["node"]), ("DC02", "N02"))
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "proposal" if followup else "scope", "type": "function",
                "function": {"name": "propose_recommendation" if followup else "resolve_scope", "arguments": "{}"},
            }]}

        with patch("backend.main.provider_key", return_value="synthetic"), patch(
                "backend.chat_agent.complete_with_tools", side_effect=propose), patch(
                "backend.main.api_key", return_value="synthetic"):
            followup = self.client.post("/api/learning-chat", json={"use_tools": True,
                "question": "给这个班生成建议", "session_id": body["session_id"]})
            self.assertEqual(followup.status_code, 200, followup.text)
            proposal = followup.json()["cards"][0]
            self.assertEqual(proposal["payload"]["snapshot_id"], snapshot)
            with store.db() as conn:
                self.assertFalse(any(job["kind"] == "generate" for job in store.all_of(conn, "job")))
            path = f"/api/chat/proposals/{proposal['id']}/confirm"
            confirmed = self.client.post(path)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(self.client.post(path).json()["result"], confirmed.json()["result"])

    def test_chat_sessions_restore_context_and_keep_result_cards(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        self.confirm(batch)
        first = self.client.post("/api/learning-chat", json={"question": "看看 DC02 最近一次测验"})
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertTrue(body["session_id"])
        self.assertEqual(body["cards"][0]["kind"], "profile")
        self.assertEqual(body["cards"][0]["target"], {"kind": "group", "id": "DC02"})
        self.assertTrue(body["cards"][0]["node"])
        session = self.client.get("/api/chat/sessions/" + body["session_id"]).json()
        self.assertEqual(session["turns"][0]["question"], "看看 DC02 最近一次测验")
        self.assertEqual(session["context"]["group"], "DC02")
        follow = self.client.post("/api/learning-chat", json={
            "question": "D01 的表现怎么样", "session_id": body["session_id"],
        })
        self.assertEqual(follow.status_code, 200, follow.text)
        self.assertEqual(follow.json()["cards"][0]["target"], {"kind": "student", "id": "D01"})
        self.assertEqual(follow.json()["context"]["student"], "D01")
        self.assertEqual(follow.json()["context"]["group"], "")
        self.assertEqual(self.client.get("/api/chat/sessions/missing").status_code, 404)

    def test_recommendation_proposal_is_idempotent_and_detects_stale_snapshot(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        first_snapshot = self.confirm(batch).json()["snapshot_id"]
        with patch("backend.main.api_key", return_value="configured"):
            chat = self.client.post("/api/learning-chat", json={"question": "帮我生成 D01 的学习建议"})
            self.assertEqual(chat.status_code, 200, chat.text)
            proposal = chat.json()["cards"][0]
            self.assertEqual(proposal["action"], "generate_recommendation")
            confirmed = self.client.post(f"/api/chat/proposals/{proposal['id']}/confirm")
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            again = self.client.post(f"/api/chat/proposals/{proposal['id']}/confirm")
            self.assertEqual(again.json()["result"], confirmed.json()["result"])
            self.assertEqual(again.json()["result"]["snapshot_id"], first_snapshot)

            stale = self.client.post("/api/learning-chat", json={"question": "帮我生成 D14 的学习建议"})
            stale_proposal = stale.json()["cards"][0]
            next_batch = self.post_import()
            self.run_cleaning(next_batch["run_id"])
            newer = self.confirm(next_batch).json()["snapshot_id"]
            self.assertNotEqual(newer, first_snapshot)
            rejected = self.client.post(f"/api/chat/proposals/{stale_proposal['id']}/confirm")
            self.assertEqual(rejected.status_code, 409)
            detail = rejected.json()["detail"]
            self.assertTrue(detail["snapshot_changed"])
            self.assertEqual(detail["current_snapshot_id"], newer)
            refreshed = self.client.post(f"/api/chat/proposals/{stale_proposal['id']}/refresh")
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(refreshed.json()["payload"]["snapshot_id"], newer)
            accepted = self.client.post(f"/api/chat/proposals/{stale_proposal['id']}/confirm")
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.json()["result"]["snapshot_id"], newer)

    def test_diagnosis_uses_original_run_scope_and_paginates_zero_events(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        original = self.confirm(batch).json()["snapshot_id"]
        with patch("backend.main.api_key", return_value="configured"):
            response = self.client.post("/api/agent/runs", json={"group": "DC02", "snapshot_id": original})
        self.assertEqual(response.status_code, 200, response.text)
        path = "/api/agent/runs/" + response.json()["run_id"] + "/diagnosis"
        next_batch = self.post_import()
        self.run_cleaning(next_batch["run_id"])
        self.confirm(next_batch)
        diagnosis = self.client.get(path).json()
        self.assertEqual(diagnosis["snapshot_id"], original)
        self.assertEqual(diagnosis["group"], "DC02")
        axis = next(axis for axis in diagnosis["indicators"] if axis["zero_count"])
        self.assertEqual(axis["distribution"]["count"], axis["coverage"])
        result = self.client.get(path + "/zero-events", params={"indicator": axis["id"], "limit": 1}).json()
        self.assertEqual(result["total"], axis["zero_count"])
        self.assertEqual(sum(group["count"] for group in result["timeline"]), result["total"])
        row = result["rows"][0]
        detail = self.client.get(path + "/zero-events", params={"indicator": axis["id"], "event": row["id"]}).json()
        self.assertEqual((detail["材料编号"], int(detail["得分"])), (row["item"], 0))
        self.assertEqual(self.client.get(path + "/zero-events", params={"event": "unknown"}).status_code, 404)

    def test_onboarding_follows_real_state_and_can_be_reopened(self):
        self.assertEqual(self.client.get("/api/onboarding").json()["state"], "empty")
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        review = self.client.get("/api/onboarding").json()
        self.assertEqual(review["state"], "review")
        self.assertEqual(review["detail"]["batch_id"], batch["batch_id"])
        self.assertEqual(review["detail"]["proposal"]["action"], "confirm_import")
        confirmed = self.confirm(batch).json()["snapshot_id"]
        state = self.client.get("/api/onboarding").json()
        self.assertEqual(state["state"], "confirmed")
        self.assertEqual(state["detail"]["snapshot_id"], confirmed)
        rows = self.client.get("/api/profiles/evidence", params={
            "snapshot_id": confirmed, "student": "D01", "node": "N01", "indicator": "T01I2"}).json()
        self.assertTrue(rows)
        viewed = self.client.get("/api/onboarding").json()
        self.assertEqual(viewed["state"], "viewed_answer")
        self.assertEqual(viewed["detail"]["student"], "D01")
        with store.db() as conn:
            draft = {"id": "approved-draft", "snapshot_id": confirmed, "approved": True, "revision": 1}
            store.put(conn, "draft", draft["id"], draft)
        completed = self.client.get("/api/onboarding").json()
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(completed["completed"])
        reopened = self.client.post("/api/onboarding/reopen").json()
        self.assertFalse(reopened["dismissed"])
        self.assertFalse(reopened["completed"])
        self.assertEqual(reopened["state"], "viewed_answer")
        self.assertTrue(self.client.post("/api/onboarding/dismiss", json={"permanent": True}).json()["dismissed"])
        self.assertTrue(self.client.get("/api/onboarding").json()["dismissed"])

    def test_cleaning_issue_export_can_target_a_pending_run(self):
        batch = self.post_import()
        self.run_cleaning(batch["run_id"])
        response = self.client.get("/api/imports/issues.csv", params={"run_id": batch["run_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("reason", response.text)
        self.assertEqual(self.client.get("/api/imports/issues.csv", params={"run_id": "missing"}).status_code, 404)
