"""Import and calculate profiles without involving the language model."""
import csv
import io
import json
import re
from collections import defaultdict
from zipfile import ZipFile

from openpyxl import load_workbook

TABLES = ("模拟学生", "课程要求", "测验节点", "题目与材料", "作答记录")
KEYS = dict(zip(TABLES, ("学生编号", "指标编号", "节点编号", "材料编号", "记录编号")))
REQUIRED = {
    "模拟学生": ("学生编号", "模拟班级编号"),
    "课程要求": ("指标编号", "主题编号"),
    "测验节点": ("节点编号", "可用题目编号"),
    "题目与材料": ("材料编号", "材料类型", "主题编号"),
    "作答记录": ("记录编号", "学生编号", "材料编号", "节点编号", "作答事件编号", "修订版本", "评分状态"),
}
ALLOWED = ("已评分", "待评分", "未作答")


def split(value):
    return [part.strip() for part in re.split("[、,，；;]", value) if part.strip()]


def open_import_workbook(raw):
    with ZipFile(io.BytesIO(raw)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 150_000_000:
            raise ValueError("工作簿解压体积过大")
    return load_workbook(io.BytesIO(raw), read_only=True, data_only=True)


def import_headers(filename, raw):
    if filename.lower().endswith(".csv"):
        try:
            contents = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            contents = raw.decode("gb18030")
        return [next(csv.reader(io.StringIO(contents, newline="")), [])]
    if filename.lower().endswith(".xlsx"):
        book = open_import_workbook(raw)
        try:
            return [next(sheet.values, ()) for sheet in list(book)[:20]]
        finally:
            book.close()
    return []


def read_file(filename, raw, mapping=None):
    def table(sheet, headers, values, first_line):
        headers = [(mapping or {}).get(str(h or "").strip(), str(h or "").strip()) for h in headers]
        if len(set(h for h in headers if h)) != len([h for h in headers if h]):
            raise ValueError("检测到重复表头")
        kind = next((name for name in reversed(TABLES) if set(REQUIRED[name]) <= set(headers)), None)
        if kind is None and {"学生编号", "测验总分"} <= set(headers):
            kind = "总分"
        if not kind:
            return []
        rows = []
        for line, row in enumerate(values, first_line):
            if not any(value is not None and str(value).strip() for value in row):
                continue
            record = {h: "" if v is None else str(v) for h, v in zip(headers, row) if h}
            rows.append({"table": kind, "source": filename, "sheet": sheet, "line": line, "values": record})
        return rows

    if filename.lower().endswith(".xlsx"):
        book = open_import_workbook(raw)
        try:
            result = []
            for sheet in book:
                values = sheet.values
                result.extend(table(sheet.title, next(values, ()), values, 2))
            if not result:
                raise ValueError("未识别到可导入的工作表，请检查列映射")
            return result
        finally:
            book.close()
    if not filename.lower().endswith(".csv"):
        raise ValueError("学情文件应为 XLSX 或 CSV")
    try:
        contents = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        contents = raw.decode("gb18030")
    values = csv.reader(io.StringIO(contents, newline=""))
    result = table("", next(values, ()), values, 2)
    if not result:
        raise ValueError("未识别到 CSV 数据，请检查列映射")
    return result


def clean(rows):
    tables = {name: {} for name in (*TABLES, "总分")}
    events = defaultdict(lambda: defaultdict(dict))
    issues = []
    states = dict.fromkeys(ALLOWED, 0)
    raw_attempts = 0

    def issue(row, reason, field=""):
        issues.append({**{k: row[k] for k in ("source", "sheet", "line", "file_id") if k in row},
                       "field": field, "original": row["values"].get(field, "") if field else row["values"],
                       "reason": reason, "included": False})

    for row in rows:
        name = row["table"]
        original = row["values"]
        values = {key: val if key in ("学生最终作答", "解答过程", "题干或内容", "参考解析") else val.strip()
                  for key, val in original.items()}
        if name == "作答记录":
            raw_attempts += 1
            try:
                version = int(values["修订版本"])
                if version < 1 or not values["作答事件编号"]:
                    raise ValueError
            except (ValueError, KeyError):
                issue(row, "缺少事件编号或修订版本无效", "修订版本")
                continue
            fingerprint = json.dumps(values, sort_keys=True, ensure_ascii=False)
            events[values["作答事件编号"]][version][fingerprint] = (row, values)
        elif name == "总分":
            try:
                score = float(values["测验总分"])
                if not 0 <= score < float("inf") or not values["学生编号"]:
                    raise ValueError
                tables[name][values["学生编号"], values.get("节点编号", "")] = values
            except ValueError:
                issue(row, "学生编号或测验总分无效", "测验总分")
        else:
            key = values.get(KEYS[name], "")
            required = REQUIRED[name]
            if name == "题目与材料" and values.get("材料类型") == "教材":
                required = ("材料编号", "材料类型")
            if not key or any(not values.get(field) for field in required):
                issue(row, "缺少必要字段", KEYS[name])
                continue
            if key in tables[name] and tables[name][key] != values:
                issue(row, "相同编号的主数据内容冲突", KEYS[name])
                continue
            tables[name][key] = values

    valid = []
    for event, versions in events.items():
        latest = list(versions[max(versions)].values())
        if len(latest) != 1:
            for row, _ in latest:
                issue(row, "同事件最高修订版本内容冲突", "作答事件编号")
            continue
        row, values = latest[0]
        state = values["评分状态"]
        if state not in states:
            issue(row, "未知评分状态", "评分状态")
            continue
        states[state] += 1
        question = tables["题目与材料"].get(values["材料编号"])
        node = tables["测验节点"].get(values["节点编号"])
        if (values["学生编号"] not in tables["模拟学生"] or not question or not node
                or values["材料编号"] not in split(node["可用题目编号"])
                or values.get("主题编号") != question.get("主题编号")
                or not set(split(question.get("指标编号", ""))) <= tables["课程要求"].keys()):
            issue(row, "学生、题目、主题、指标或测验范围无法关联", "材料编号")
            continue
        try:
            maximum = int(values.get("满分", ""))
            if maximum < 1 or maximum != int(question["满分"]):
                raise ValueError
            if state == "已评分":
                score = int(values["得分"])
                if not 0 <= score <= maximum:
                    raise ValueError
                rubric = {point: int(points) for point, points in re.findall(
                    r"([A-Za-z0-9]+)\s*.*?\s+(\d+)分", question.get("评分点", ""))}
                points = [piece.split("=", 1) for piece in split(values.get("评分点得分", ""))]
                ids = {key for key, _ in points}
                if points and (len(ids) != len(points) or ids != rubric.keys()
                               or any(not 0 <= int(p) <= rubric[k] for k, p in points)
                               or sum(int(p) for _, p in points) != score):
                    raise ValueError
            elif values.get("得分") or values.get("评分点得分"):
                raise ValueError
        except (ValueError, KeyError):
            issue(row, "题目满分、得分或评分点不匹配", "得分")
            continue
        values["指标"] = split(question.get("指标编号", ""))
        values["来源"] = {k: row[k] for k in ("source", "sheet", "line")}
        valid.append(values)
    tables["作答记录"] = {a["作答事件编号"]: a for a in valid}
    return tables, {"raw_attempts": raw_attempts, "events": len(events), "states": states,
                    "valid_scored": sum(r["评分状态"] == "已评分" for r in valid),
                    "students": len(tables["模拟学生"]), "issues": issues}


def profile(tables, student="", group="", node="", include_question_scope=False):
    people = {key for key, row in tables["模拟学生"].items()
              if (not group or row["模拟班级编号"] == group) and (not student or key == student)}
    nodes = [row for key, row in tables["测验节点"].items() if not node or key == node]
    range_ids = {q for row in nodes for q in split(row["可用题目编号"])}
    attempts = [r for r in tables["作答记录"].values()
                if r["学生编号"] in people and (not node or r["节点编号"] == node)]
    eligible_questions = [q for key, q in tables["题目与材料"].items() if key in range_ids]
    axes = []
    for key, indicator in tables["课程要求"].items():
        axes.append(axis(key, indicator.get("观察指标", key), attempts, people, eligible_questions, include_question_scope))
    topics = {}
    for row in tables["课程要求"].values():
        topics[row["主题编号"]] = row.get("主题", row["主题编号"])
    return {"student": student, "group": group, "node": node, "size": len(people),
            "axes": axes, "topics": [axis(k, name, attempts, people, eligible_questions, include_question_scope)
                                      for k, name in topics.items()],
            "totals": [r for r in tables["总分"].values() if r["学生编号"] in people and (not node or r.get("节点编号") == node)]}


def student_scores(scored):
    totals = defaultdict(lambda: [0, 0, 0])
    for row in scored:
        student = totals[row["学生编号"]]
        student[0] += int(row["得分"])
        student[1] += int(row["满分"])
        student[2] += 1
    return totals


def axis(key, label, attempts, people, eligible_questions, include_question_scope=False):
    topic = len(key) == 3
    def matches(record, indicators):
        return record.get("主题编号") == key if topic else key in indicators
    observed = [r for r in attempts if matches(r, r["指标"])]
    scored = [r for r in observed if r["评分状态"] == "已评分"]
    per_person = student_scores(scored)
    rates = [100 * s / m for s, m, _ in per_person.values()]
    distribution = sorted(round(rate, 1) for rate in rates)
    question_ids = [q["材料编号"] for q in eligible_questions if matches(q, split(q.get("指标编号", "")))]
    return {"id": key, "label": label, "score": round(sum(rates) / len(rates), 1) if rates else None,
            "earned": sum(s for s, _, _ in per_person.values()), "possible": sum(m for _, m, _ in per_person.values()),
            "n": len(scored), "coverage": len(per_person), "population": len(people), "distribution": distribution,
            "status": "已评分" if scored else ("未教学" if not question_ids else "无有效评分"),
            **({"question_ids": question_ids} if include_question_scope else {}),
            "states": {state: sum(r["评分状态"] == state for r in observed) for state in ALLOWED},
            "attempt_ids": [r["作答事件编号"] for r in scored]}
