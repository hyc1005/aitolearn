from pathlib import Path
from unittest import TestCase

from backend.data import TABLES, clean, profile, read_file


class CompetitionDataTests(TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path("ori_data")
        files = [next(directory.rglob(name + ".csv"), None) for name in TABLES]
        if not all(files):
            from unittest import SkipTest
            raise SkipTest("赛题资料未复制到 ori_data")
        cls.rows = [row for file in files for row in read_file(file.name, file.read_bytes())]
        cls.data, cls.report = clean(cls.rows)

    def test_revision_counts(self):
        self.assertEqual(self.report["raw_attempts"], 806)
        self.assertEqual(self.report["events"], 799)
        self.assertEqual(self.report["states"], {"已评分": 790, "待评分": 4, "未作答": 5})
        self.assertEqual(self.report["valid_scored"], 785)
        self.assertEqual(len(self.report["issues"]), 5)
        self.assertEqual(len(self.data["题目与材料"]), 65)

    def test_workbook_matches_csv(self):
        file = next(Path("ori_data").rglob("*.xlsx"))
        data, report = clean(read_file(file.name, file.read_bytes()))
        self.assertEqual(report["states"], self.report["states"])
        self.assertEqual(profile(data, student="D01"), profile(self.data, student="D01"))

    def test_nodes_and_evidence(self):
        first = profile(self.data, student="D01", node="N01")
        self.assertEqual(next(a for a in first["axes"] if a["id"] == "T03I1")["status"], "未教学")
        key = "D01-N01-Q015"
        record = self.data["作答记录"][key]
        self.assertEqual(record["学生最终作答"], "4a+7b")
        self.assertIn("4a-7b", record["解答过程"])
        self.assertIn(key, next(a for a in first["axes"] if a["id"] == "T01I2")["attempt_ids"])

    def test_conflicting_latest_attempt_is_quarantined(self):
        source = next(r for r in self.rows if r["values"].get("记录编号") == "DR0001")
        conflict = {**source, "line": 9001, "values": {**source["values"], "得分": "1"}}
        data, report = clean([*self.rows, conflict])
        self.assertNotIn("D01-N01-Q015", data["作答记录"])
        self.assertEqual(sum("最高修订版本" in r["reason"] for r in report["issues"]), 2)
