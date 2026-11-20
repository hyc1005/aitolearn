import base64
import csv
import hashlib
import hmac
import io
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import api_key
from .data import profile, read_file
from .recommendation_review import preview_text
from .search import search
from .snapshots import current_snapshot, legacy_batches, queue_cleaning, queue_job, table_data
from .store import all_of, db, get, new_id, put, save_file


@asynccontextmanager
async def lifespan(app):
    with db() as conn:
        for item in all_of(conn, "delivery"):
            if item["state"] == "sending":
                put(conn, "delivery", item["id"], {**item, "state": "unknown"})
    yield


app = FastAPI(title="智慧学伴", lifespan=lifespan)


@app.middleware("http")
async def local_origin(request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0]
    origin = urlparse(request.headers.get("origin", "")).hostname
    if host not in {"127.0.0.1", "localhost", "testserver"} or (
        origin and origin not in {"127.0.0.1", "localhost", "testserver"}
    ):
        return Response(status_code=403)
    return await call_next(request)


def require(conn, kind, key):
    item = get(conn, kind, key)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def tables(conn):
    return table_data(current_snapshot(conn))


def job(conn, kind, payload):
    return queue_job(conn, kind, payload)


async def read_upload(file):
    raw = await file.read(70_000_001)
    if not raw or len(raw) > 70_000_000:
        raise HTTPException(400, "文件为空或超过 70 MB")
    return raw


@app.get("/api/health")
def health():
    return {"status": "ready", "model_configured": bool(api_key()),
            "ocr_configured": Path(".venv-ocr/Scripts/python.exe").exists()}


@app.get("/api/jobs")
def jobs():
    with db() as conn:
        return [{field: value for field, value in item.items() if field not in {"stages", "failures"}}
                for item in sorted(all_of(conn, "job"), key=lambda job: -job["created"])]


@app.get("/api/agent/runs/{key}")
def run_detail(key: str):
    with db() as conn:
        run = require(conn, "job", key)
        if run["kind"] not in {"clean", "generate", "recommendation_review"}:
            raise HTTPException(404, "Agent 运行不存在")
        stages = run.get("stages", {})
        if run["kind"] == "clean" and "python" in stages:
            snapshot = require(conn, "snapshot", stages["python"]["snapshot_id"])
            active = current_snapshot(conn)
            run.update(report=snapshot["report"], manifest=snapshot["manifest"],
                       outdated=(active["id"] if active else None) != snapshot["base_snapshot_id"],
                       correction=require(conn, "import", run["payload"]["batch_id"]).get("correction", False))
        if "interpret" in stages:
            data = table_data(require(conn, "snapshot", run["payload"]["snapshot_id"]))
            refs = {ref for claim in stages["interpret"]["claims"] for ref in claim["refs"][1:]}
            run["linked_evidence"] = {ref: {**data["作答记录"][ref],
                                          "题目": data["题目与材料"][data["作答记录"][ref]["材料编号"]]}
                                      for ref in refs}
        return run


@app.get("/api/agent/cases/q015")
def q015_case():
    draft_id = "2809aa96e95e4568a990e4f10828b44a"
    review_id = "9cbafe0ffab2448090a8776ed9ae7229"
    with db() as conn:
        conn.execute("BEGIN")
        initial_run = get(conn, "job", draft_id) or {}
        final_run = get(conn, "job", review_id) or {}
        first = get(conn, "draft_revision", f"{draft_id}:1")
        second = get(conn, "draft_revision", f"{draft_id}:2")
        snapshot_id = (initial_run.get("payload") or {}).get("snapshot_id")
        snapshot_id = snapshot_id or (first or second or {}).get("snapshot_id")
        snapshot = get(conn, "snapshot", snapshot_id) if snapshot_id else None

    evidence = None
    if snapshot:
        data = table_data(snapshot)
        row = data["作答记录"].get("D01-N01-Q015")
        if row:
            evidence = {**row, "题目": data["题目与材料"].get("Q015", {})}

    def suggestion(draft):
        if draft is None:
            return None
        return {field: draft.get(field) for field in
                ("revision", "observation", "message", "exercises", "citations")}

    def review(run, draft, revision):
        if (run.get("result") or {}) != {"draft": draft_id, "revision": revision}:
            return None
        result = (run.get("stages") or {}).get("final_review")
        if not result or result.get("input_version") != revision:
            return None
        return {"review": result, "confirmed": bool(
            draft and draft.get("approved") and run.get("confirmed_at")), "run_id": run["id"]}

    return {"snapshot_id": snapshot_id, "steps": [
        {"title": "D01 · N01 · Q015 作答", "evidence": evidence},
        {"title": "初版建议", "before": suggestion(first)},
        {"title": "初次终审", "result": review(initial_run, first, 1)},
        {"title": "教师修订", "before": suggestion(first), "after": suggestion(second)},
        {"title": "修订 2 终审", "result": review(final_run, second, 2)},
    ]}


@app.post("/api/jobs/{key}/retry")
def retry(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = require(conn, "job", key)
        if item["state"] != "failed":
            raise HTTPException(409, "仅失败任务可以重试")
        if item["kind"] not in {"clean", "generate", "recommendation_review"}:
            return job(conn, item["kind"], item["payload"])
        item["state"] = "queued"
        item.pop("error", None)
        put(conn, "job", key, item)
        return item


@app.post("/api/imports")
async def stage(files: list[UploadFile] = File(...), mapping: str = Form("{}")):
    rows, sources = [], []
    try:
        columns = json.loads(mapping)
        if not isinstance(columns, dict) or not all(isinstance(v, str) for v in columns.values()):
            raise ValueError("列映射必须是 JSON 对象")
        for file in files:
            name = Path(file.filename or "").name
            raw = await read_upload(file)
            parsed = read_file(name, raw, columns)
            file_id = new_id()
            rows.extend({**row, "file_id": file_id} for row in parsed)
            sources.append({"id": file_id, "name": name, "hash": hashlib.sha256(raw).hexdigest(),
                            "path": save_file(raw, Path(name).suffix.lower())})
    except (ValueError, OSError, csv.Error) as exc:
        raise HTTPException(400, str(exc)) from exc
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = {"id": new_id(), "rows": rows, "sources": sources, "mapping": columns,
                 "label": "、".join(source["name"] for source in sources), "confirmed": False}
        return cleaning_run(queue_cleaning(conn, batch))


def cleaning_run(item):
    return {"batch_id": item["payload"]["batch_id"], "run_id": item["id"]}


@app.post("/api/imports/migrate")
def migrate_imports():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        baseline = legacy_batches(conn)
        if current_snapshot(conn) or not baseline:
            raise HTTPException(409, "没有待迁移的历史批次")
        for item in all_of(conn, "job"):
            if item["kind"] == "clean" and item["payload"].get("label") == "迁移历史批次":
                return cleaning_run(item)
        batch = {"id": new_id(), "rows": [], "sources": [], "label": "迁移历史批次", "confirmed": False}
        return cleaning_run(queue_cleaning(conn, batch))


@app.post("/api/imports/{key}/rebase")
def rebase_import(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require(conn, "import", key)
        if batch.get("confirmed") or batch.get("correction"):
            raise HTTPException(409, "已确认批次不可重建，教师修订请从当前异常重新发起")
        active = current_snapshot(conn)
        active_id = active["id"] if active else None
        for item in reversed(all_of(conn, "job")):
            payload = item["payload"]
            if (item["kind"] == "clean" and payload["batch_id"] == key
                    and payload["base_snapshot_id"] == active_id):
                return cleaning_run(item)
        return cleaning_run(queue_cleaning(conn, batch))


@app.post("/api/imports/{key}/confirm")
def confirm(key: str, run_id: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = require(conn, "import", key)
        run = require(conn, "job", run_id)
        if run["kind"] != "clean" or run["payload"]["batch_id"] != key:
            raise HTTPException(409, "任务与批次不匹配")
        if run.get("stage") == "published":
            return run["result"]
        if run["state"] != "done" or run.get("stage") != "confirm":
            raise HTTPException(409, "请先完成模型清洗复核")
        active = current_snapshot(conn)
        baseline = run["payload"]["base_snapshot_id"]
        if baseline != (active["id"] if active else None) or (
                active is None and run["payload"]["legacy_baseline"] != legacy_batches(conn)):
            raise HTTPException(409, "基础数据已变更，请按当前数据重新运行清洗")
        new_snapshot = require(conn, "snapshot", run["result"]["snapshot_id"])
        put(conn, "settings", "active_snapshot", {"id": new_snapshot["id"], "review_run_id": run_id})
        put(conn, "import", key, {**item, "confirmed": True, "snapshot_id": new_snapshot["id"]})
        put(conn, "job", run_id, {**run, "stage": "published", "confirmed_at": time.time()})
        return run["result"]


def cleaning_report(conn):
    snapshot = current_snapshot(conn)
    if snapshot:
        run = require(conn, "job", snapshot["review_run_id"])
        return {**snapshot["report"], "snapshot_id": snapshot["id"],
                "last_agent_review": run["stages"]["clean_review"]}
    return {"raw_attempts": 0, "events": 0, "valid_scored": 0, "issues": [], "states": {},
            "legacy_pending": bool(legacy_batches(conn))}


@app.get("/api/imports/report")
def report():
    with db() as conn:
        return cleaning_report(conn)


class Correction(BaseModel):
    source: str
    sheet: str
    line: int
    file_id: str | None = None
    changes: dict[str, str]


@app.post("/api/imports/corrections")
def correct_record(edit: Correction):
    allowed = {"学生编号", "材料编号", "主题编号", "节点编号", "评分状态", "得分", "满分", "评分点得分"}
    if not edit.changes or not edit.changes.keys() <= allowed:
        raise HTTPException(400, "仅支持修正作答关联和评分字段")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = current_snapshot(conn)
        if not active:
            raise HTTPException(409, "请先确认数据快照")
        rows = [row for key in active["batch_ids"] for row in require(conn, "import", key)["rows"]]
        candidates = [r for r in rows if r["table"] == "作答记录"
                      and (r["source"], r["sheet"], r["line"]) == (edit.source, edit.sheet, edit.line)
                      and (edit.file_id is None or r.get("file_id") == edit.file_id)]
        if len(candidates) != 1 or not candidates[0]["values"].get("作答事件编号"):
            raise HTTPException(409, "无法唯一定位该作答事件")
        source = candidates[0]
        event = source["values"]["作答事件编号"]
        version = max((int(r["values"]["修订版本"]) for r in rows
                       if r["table"] == "作答记录" and r["values"]["作答事件编号"] == event
                       and r["values"]["修订版本"].isdigit()), default=0) + 1
        key = new_id()
        values = {**source["values"], **edit.changes, "修订版本": str(version), "记录编号": key}
        revision = {**source, "values": values, "source": "教师修订-" + key[:8]}
        original = {field: source[field] for field in ("source", "sheet", "line", "file_id") if field in source}
        batch = {"id": key, "confirmed": False, "rows": [revision], "sources": [],
                 "correction": True, "revision_of": original, "label": revision["source"]}
        return cleaning_run(queue_cleaning(conn, batch))


@app.get("/api/imports/issues.csv")
def issues_csv():
    with db() as conn:
        report = cleaning_report(conn)
        issues = [{"snapshot_id": report["snapshot_id"], **issue} for issue in report["issues"]]
    return csv_response(issues, "cleaning-issues.csv")


def csv_response(rows, filename):
    output = io.StringIO()
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("'" + val if isinstance(val, str) and val.startswith(("=", "+", "-", "@", "\t", "\r")) else val)
                         for key, val in row.items()})
    return Response("\ufeff" + output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/curriculum")
def curriculum():
    with db() as conn:
        data = tables(conn)
        return {"indicators": list(data["课程要求"].values()), "nodes": list(data["测验节点"].values()),
                "questions": [{k: row.get(k) for k in ("材料编号", "主题编号", "指标编号", "题干或内容", "来源或资料文件")}
                              for row in data["题目与材料"].values()]}


@app.get("/api/profiles")
def profiles(student: str = "", group: str = "", node: str = ""):
    with db() as conn:
        active = current_snapshot(conn)
        data = table_data(active)
    students = list(data["模拟学生"].values())
    groups = sorted({r["模拟班级编号"] for r in students})
    return {"profile": profile(data, student, group, node), "students": students,
            "groups": [profile(data, group=g, node=node) for g in groups],
            "nodes": list(data["测验节点"].keys()), "snapshot_id": active["id"] if active else None}


@app.get("/api/profiles/evidence")
def evidence(indicator: str, student: str = "", group: str = "", node: str = "", snapshot_id: str = ""):
    with db() as conn:
        data = table_data(require(conn, "snapshot", snapshot_id) if snapshot_id else current_snapshot(conn))
    snapshot = profile(data, student, group, node)
    selected = next((a for a in snapshot["axes"] + snapshot["topics"] if a["id"] == indicator), None)
    if selected is None:
        raise HTTPException(404, "指标不存在")
    return [{**row, "题目": data["题目与材料"][row["材料编号"]]} for key in selected["attempt_ids"]
            if (row := data["作答记录"].get(key))]


@app.get("/api/materials")
def materials():
    with db() as conn:
        return [{k: doc[k] for k in ("id", "name", "state")} for doc in all_of(conn, "material")]


@app.post("/api/materials")
async def upload_material(file: UploadFile = File(...)):
    name = Path(file.filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "请上传 PDF、Word 或照片")
    path = save_file(await read_upload(file), suffix)
    with db() as conn:
        key = new_id()
        put(conn, "material", key, {"id": key, "name": name, "state": "processing", "path": path,
                                     "blocks": [], "previews": {}})
        return job(conn, "extract", {"material": key})


@app.post("/api/materials/seed-text")
def seed_text():
    with db() as conn:
        data = tables(conn)
        key = new_id()
        blocks = []
        for row in data["题目与材料"].values():
            text = "\n".join(f"{k}：{row[k]}" for k in ("题干或内容", "参考答案", "参考解析") if row.get(k))
            if text:
                blocks.append({"id": new_id(), "text": text, "locator": row["材料编号"], "material": key,
                               "title": "赛题题库与学习卡", "bbox": None})
        if not blocks:
            raise HTTPException(400, "请先导入题目与材料表")
        put(conn, "material", key, {"id": key, "name": "赛题题库与学习卡", "state": "review",
                                     "blocks": blocks, "previews": {}})
        return {"id": key}


@app.get("/api/materials/search")
def search_material(query: str):
    with db() as conn:
        return search(conn, query)


@app.get("/api/materials/{key}")
def material(key: str):
    with db() as conn:
        doc = require(conn, "material", key)
        return {**{k: doc[k] for k in ("id", "name", "state")},
                "locators": list(doc["previews"]),
                "blocks": [{k: v for k, v in block.items() if k != "vector"} for block in doc["blocks"]]}


@app.get("/api/materials/{key}/preview")
def preview(key: str, locator: str):
    with db() as conn:
        doc = require(conn, "material", key)
    if locator not in doc["previews"]:
        raise HTTPException(404, "该位置无页图")
    return FileResponse(doc["previews"][locator])


class Revision(BaseModel):
    changes: dict[str, str]


@app.put("/api/materials/{key}")
def edit_material(key: str, revision: Revision):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        doc = require(conn, "material", key)
        if doc["state"] not in {"review", "published"}:
            raise HTTPException(409, "该资料尚不可编辑")
        if doc["state"] == "published":
            key = new_id()
            doc = {**doc, "id": key, "replaces": doc["id"], "state": "review"}
            for block in doc["blocks"]:
                old = block["id"]
                block.update(id=new_id(), material=key, text=revision.changes.get(old, block["text"]))
                block.pop("vector", None)
        else:
            for block in doc["blocks"]:
                block["text"] = revision.changes.get(block["id"], block["text"])
        put(conn, "material", key, doc)
        return {"id": key}


@app.post("/api/materials/{key}/publish")
def publish(key: str):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        doc = require(conn, "material", key)
        if doc["state"] != "review" or not any(c["text"].strip() for c in doc["blocks"]):
            raise HTTPException(409, "资料尚不可发布")
        doc["state"] = "indexing"
        put(conn, "material", key, doc)
        return job(conn, "publish", {"material": key})


class Run(BaseModel):
    student: str = ""
    group: str = ""
    node: str = ""
    variant: bool = False
    anchor_event: str = ""
    snapshot_id: str = ""


@app.post("/api/agent/runs")
def start_run(data: Run):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = current_snapshot(conn)
        if not snapshot:
            raise HTTPException(409, "请先确认数据快照")
        if data.snapshot_id and data.snapshot_id != snapshot["id"]:
            raise HTTPException(409, "数据快照已更新，请刷新画像后重新发起")
        source = table_data(snapshot)
        if not api_key():
            raise HTTPException(400, "请配置模型密钥")
        if bool(data.student) == bool(data.group):
            raise HTTPException(400, "请选择单个学生或班级")
        if (data.student and data.student not in source["模拟学生"]) or (
            data.group and data.group not in {r["模拟班级编号"] for r in source["模拟学生"].values()}
        ) or (data.node and data.node not in source["测验节点"]):
            raise HTTPException(404, "学生、班级或测验不存在")
        if data.anchor_event:
            row = source["作答记录"].get(data.anchor_event)
            if (not row or not data.student or row["学生编号"] != data.student
                    or row["评分状态"] != "已评分" or not row["指标"]
                    or (data.node and row["节点编号"] != data.node)):
                raise HTTPException(400, "指定作答不属于当前学生和测验的有效证据")
        material_ids = [doc["id"] for doc in all_of(conn, "material") if doc["state"] == "published"]
        item = job(conn, "generate", {**data.model_dump(), "snapshot_id": snapshot["id"],
                                      "material_ids": material_ids})
        return {**item, "run_id": item["id"]}


@app.get("/api/recommendations")
def recommendations():
    with db() as conn:
        return all_of(conn, "draft")


class Edit(BaseModel):
    revision: int
    exercises: list[str]
    message: str = Field(max_length=5000)
    observation: str | None = Field(default=None, max_length=5000)
    uncertainty: str | None = Field(default=None, max_length=5000)
    hints: dict[str, list[str]] = Field(default_factory=dict)
    teaching_texts: list[str] | None = None
    approved: bool


@app.put("/api/recommendations/{key}")
def edit(key: str, edit: Edit):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = require(conn, "draft", key)
        if edit.revision != draft["revision"]:
            raise HTTPException(409, "建议已更新，请刷新")
        by_id = {q["question_id"]: q for q in draft["exercises"]}
        if set(edit.exercises) != by_id.keys() or len(edit.exercises) != len(by_id):
            raise HTTPException(400, "练习排序无效")
        if set(edit.hints) - by_id.keys() or any(
                not 1 <= len(hints) <= 4 or any(not hint.strip() or len(hint) > 1200 for hint in hints)
                for hints in edit.hints.values()):
            raise HTTPException(400, "每道练习需要 1 至 4 条有效提示")
        exercises = [{**by_id[key], "hints": edit.hints.get(key, by_id[key]["hints"])}
                     for key in edit.exercises]
        before = [q["question_id"] for q in draft["exercises"]]
        changed = edit.exercises != before or edit.message != draft.get("message", draft.get("follow_up", ""))
        changed = changed or any(hints != by_id[key]["hints"] for key, hints in edit.hints.items())
        copy = {field: val for field in ("observation", "uncertainty") if (val := getattr(edit, field)) is not None}
        if edit.teaching_texts is not None:
            points = draft.get("teaching_points", [])
            if len(edit.teaching_texts) != len(points) or any(
                    not line.strip() or len(line) > 800 for line in edit.teaching_texts):
                raise HTTPException(400, "请保留各条教学说明及其引用")
            copy["teaching_points"] = [{**point, "text": line}
                                       for point, line in zip(points, edit.teaching_texts)]
        changed = changed or any(value != draft.get(field) for field, value in copy.items())
        if changed:
            if not draft.get("snapshot_id"):
                raise HTTPException(409, "历史建议未登记证据版本，请重新生成")
            draft.update(exercises=exercises, message=edit.message,
                         revision=draft["revision"] + 1, approved=False, updated=time.time(),
                         review_state="pending", **copy)
            draft.pop("final_review", None)
            item = job(conn, "recommendation_review", {"draft": key, "revision": draft["revision"],
                                                        "snapshot_id": draft.get("snapshot_id"),
                                                        "student": draft["student"], "group": draft["group"]})
            draft["review_run_id"] = item["id"]
        elif edit.approved:
            review_job = get(conn, "job", draft.get("review_run_id", ""))
            if (not draft.get("snapshot_id") or draft.get("review_state") != "pass"
                    or not review_job or review_job["state"] != "done"):
                raise HTTPException(409, "当前建议版本需先通过模型终审")
            draft.update(approved=True, updated=time.time())
            put(conn, "job", review_job["id"], {**review_job, "stage": "published",
                                                "confirmed_at": time.time()})
        put(conn, "draft", key, draft)
        put(conn, "draft_revision", f'{key}:{draft["revision"]}', draft)
        return draft


@app.get("/api/recommendations/{key}/preview")
def student_preview(key: str):
    with db() as conn:
        draft = require(conn, "draft", key)
        binding = get(conn, "binding", draft["student"])
    return {"text": preview_text(draft), "group": binding["label"] if binding else None}


class Binding(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    webhook: str
    secret: str = ""


@app.put("/api/deliveries/binding/{student}")
def bind(student: str, data: Binding):
    import keyring

    url = urlparse(data.webhook)
    token = url.path.removeprefix("/open-apis/bot/v2/hook/")
    if url.scheme != "https" or url.netloc != "open.feishu.cn" or "?" in data.webhook or url.fragment or (
        not url.path.startswith("/open-apis/bot/v2/hook/") or not token or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in token)):
        raise HTTPException(400, "请输入飞书自定义机器人 HTTPS webhook")
    fingerprint = hashlib.sha256(data.webhook.encode()).hexdigest()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if student not in tables(conn)["模拟学生"]:
            raise HTTPException(404, "学生不存在")
        if any(b["fingerprint"] == fingerprint and b["student"] != student for b in all_of(conn, "binding")):
            raise HTTPException(409, "该群 webhook 已绑定其他学生")
        credential = f"{student}:{fingerprint}"
        keyring.set_password("aitolearn-feishu", credential, json.dumps(data.model_dump()))
        put(conn, "binding", student, {"student": student, "label": data.label, "fingerprint": fingerprint,
                                        "credential": credential})
    return {"student": student, "label": data.label}


def send_to(binding, text):
    import keyring

    raw = keyring.get_password("aitolearn-feishu", binding["credential"])
    if not raw:
        return "failed", "凭据缺失"
    data = json.loads(raw)
    payload = {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "智慧学伴 · 学习建议"}},
                 "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": text}}]}}
    if data["secret"]:
        stamp = str(int(time.time()))
        sign = hmac.digest(f'{stamp}\n{data["secret"]}'.encode(), b"", "sha256")
        payload.update(timestamp=stamp, sign=base64.b64encode(sign).decode())
    try:
        res = httpx.post(data["webhook"], json=payload, timeout=10)
        if res.status_code >= 500:
            return "unknown", "飞书服务错误，请核对收件群"
        if res.is_success:
            result = res.json()
            if not isinstance(result, dict):
                return "unknown", "飞书响应格式异常，请核对收件群"
            if result.get("code", result.get("StatusCode")) == 0:
                return "sent", ""
        return "failed", "飞书拒绝请求，请检查机器人配置"
    except (httpx.HTTPError, ValueError):
        return "unknown", "未收到可确认的响应，请核对收件群"


@app.post("/api/deliveries/test/{student}")
def test_binding(student: str):
    with db() as conn:
        binding = require(conn, "binding", student)
    state, message = send_to(binding, f"专属辅导群绑定测试 · {student}")
    return {"state": state, "message": message}


@app.post("/api/deliveries/send/{key}")
def send(key: str, revision: int):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = require(conn, "draft", key)
        if not draft["approved"] or draft["revision"] != revision:
            raise HTTPException(409, "请先确认当前版本")
        binding = require(conn, "binding", draft["student"])
        text = preview_text(draft)
        if not text:
            raise HTTPException(400, "卡片内容为空")
        uid = f"{key}:{revision}"
        existing = get(conn, "delivery", uid)
        if existing and existing["state"] != "failed":
            return existing
        item = {"id": uid, "draft": key, "student": draft["student"], "group": binding["label"],
                "state": "sending", "time": time.time()}
        put(conn, "delivery", uid, item)
    item["state"], item["message"] = send_to(binding, text)
    with db() as conn:
        put(conn, "delivery", uid, item)
    return item


@app.get("/api/deliveries")
def deliveries():
    with db() as conn:
        return all_of(conn, "delivery")


@app.get("/api/export")
def export_data(format: str = "json", formal: bool = False):
    with db() as conn:
        snapshot = current_snapshot(conn)
        snapshot_id = snapshot["id"] if snapshot else None
        data = table_data(snapshot)
        people = data["模拟学生"]
        groups = sorted({r["模拟班级编号"] for r in people.values()})
        drafts = all_of(conn, "draft")
        if formal:
            if format != "json" or not snapshot:
                raise HTTPException(409, "正式成果需要已确认的数据快照，使用 JSON 导出")
            drafts = [item for item in drafts if item.get("snapshot_id") == snapshot_id
                      and item["approved"] and item.get("final_review", {}).get("decision") == "pass"
                      and item["final_review"]["input_version"] == item["revision"]]
    learners = [profile(data, student=key) for key in people]
    classes = [profile(data, group=key) for key in groups]
    if format == "csv":
        return csv_response([{"snapshot_id": snapshot_id, "student": p["student"], "class": p["group"], "indicator": a["id"],
                              "score": a["score"], "earned": a["earned"], "possible": a["possible"],
                              "question_count": a["n"], "coverage": a["coverage"], "status": a["status"]}
                             for p in learners + classes for a in p["axes"]], "profiles.csv")
    payload = {"snapshot_id": snapshot_id, "curriculum": list(data["课程要求"].values()), "students": learners, "classes": classes,
               "recommendations": drafts, "fields": {"score": "有效作答得分率（百分比）",
                   "coverage": "具备有效作答的学生数", "attempt_ids": "支持该指标计算的作答事件编号"}}
    if formal:
        payload["manifest"] = {
            "snapshot_id": snapshot_id, "rules_version": snapshot["rules_version"],
            "input_batches": snapshot["manifest"], "clean_review_run_id": snapshot["review_run_id"],
            "recommendations": [{"id": item["id"], "revision": item["revision"],
                                 "review_run_id": item["review_run_id"], "material_ids": item["material_ids"]}
                                for item in drafts],
        }
    return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="aitolearn.json"'})


if Path("frontend/dist/index.html").exists():
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
