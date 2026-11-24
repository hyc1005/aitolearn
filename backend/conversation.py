"""Chat sessions, grounded answer cards, proposals and first-use guidance.

The language model may propose an operation, but only the small set below is
accepted.  Every object, batch and snapshot is resolved again against the
confirmed data before a proposal is stored or confirmed.
"""
import hashlib
import json
import re
import time

from .student_query import profile
from .models import active_provider, provider_key
from .snapshots import current_snapshot, table_data
from .store import all_of, get, new_id, put

SUPPORTED = {"generate_recommendation", "confirm_import", "publish_material", "retry_job",
             "correct_attempt", "revise_draft", "revise_material"}
CORRECTION_FIELDS = {"学生编号", "材料编号", "主题编号", "节点编号", "评分状态", "得分", "满分", "评分点得分"}
MAX_TURNS = 60
STUDENT_ID = re.compile(r"(?<![A-Z0-9])([A-Z]{1,4}\d{2,4})(?![A-Z0-9])")
NODE_ID = re.compile(r"(?<![A-Z0-9])(N\d{1,4})(?![A-Z0-9])")
ITEM_ID = re.compile(r"(?<![A-Z0-9])(Q\d{1,4})(?![A-Z0-9])")
LATEST = re.compile(r"最近|最新|上次|这一次|本次|这次")
CLEANING = re.compile(r"清洗|导入|上传|进度|复核|确认.{0,6}(数据|快照)|异常")
RECOMMEND = re.compile(r"生成.{0,4}(学习)?建议|学习建议|练习建议|出.{0,3}建议|制定.{0,4}建议")
VIEW = re.compile(r"看看|查看|看一下|查查|表现|怎么样|哪里|薄弱|最弱|最需要|弱项|需要讲|得分率|最近一次")
EXPLAIN = re.compile(r"依据|为什么|原因|原文|解释|根据|去括号|怎么算")
CONFIRM = re.compile(r"确认.{0,8}(数据|快照|这批)|核对")


def settings_get(conn, key, default=None):
    return get(conn, "settings", key) or default


def settings_put(conn, key, value):
    put(conn, "settings", key, value)


def node_order(data):
    details = [row for row in data["测验节点"].values()]
    details.sort(key=lambda row: (row.get("模拟测验日期", ""), row["节点编号"]))
    return [row["节点编号"] for row in details]


def resolve(data, question, context=None):
    """Resolve objects mentioned in a question without guessing."""
    context = context or {}
    text = question.upper()
    students = list(data["模拟学生"])
    groups = sorted({row["模拟班级编号"] for row in data["模拟学生"].values()})
    nodes = node_order(data)
    found_students = [key for key in students if re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])", text)]
    found_groups = [key for key in groups if re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])", text)]
    found_nodes = [key for key in nodes if re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])", text)]
    student = context.get("student", "")
    group = context.get("group", "")
    node = context.get("node", "")
    if found_students:
        student, group = found_students[0], ""
    elif found_groups:
        group, student = found_groups[0], ""
    if found_nodes:
        node = found_nodes[0]
    elif LATEST.search(question) and nodes:
        node = nodes[-1]
    item = (ITEM_ID.search(question.upper()) or [None])[0]
    candidates = []
    if len(found_students) > 1:
        candidates = [{"kind": "student", "id": key, "label": key + " · " + data["模拟学生"][key]["模拟班级编号"]}
                      for key in found_students]
    elif len(found_groups) > 1:
        candidates = [{"kind": "group", "id": key, "label": key + " · 班级"} for key in found_groups]
    return {"student": student, "group": group, "node": node, "item": item or "",
            "candidates": candidates, "students": students, "groups": groups, "nodes": nodes}


def profile_card(conn, data, snapshot_id, student="", group="", node="", item=""):
    snapshot = profile(conn, snapshot_id, data, student=student, group=group, node=node, include_question_scope=True)
    axes = sorted(snapshot["axes"], key=lambda axis: (axis["score"] is None, axis["score"] or 0))
    topic = ""
    if item:
        question = data["题目与材料"].get(item) or {}
        topic = question.get("主题编号", "")
    selected = [axis for axis in axes if not topic or item in axis.get("question_ids", [])]
    weakest = next((axis for axis in selected if axis["score"] is not None), None)
    return {
        "kind": "profile",
        "snapshot_id": snapshot_id,
        "target": {"kind": "student" if student else "group", "id": student or group},
        "node": node,
        "student": student,
        "group": group or (data["模拟学生"].get(student, {}).get("模拟班级编号", "") if student else ""),
        "item": item,
        "weakest": weakest,
        "indicators": selected[:8],
        "size": snapshot["size"],
    }


def summary_answer(card, question):
    target = card["target"]["id"]
    where = (" · " + card["node"]) if card["node"] else ""
    indicators = [axis for axis in card["indicators"] if axis["score"] is not None]
    if card["target"]["kind"] == "student":
        if not indicators:
            return f"{target}{where} 暂无有效已评分作答，无法给出得分率。"
        parts = [f"{axis['label']} {axis['score']}%" for axis in indicators[:4]]
        return f"{target}{where} 已读取有效作答。" + "；".join(parts) + "。可从指标打开原始作答。"
    if not indicators:
        return f"{target}{where} 暂无有效已评分作答，无法判断薄弱项。"
    lines = []
    for axis in indicators[:3]:
        lines.append(f"{axis['label']} {axis['score']}%（覆盖 {axis['coverage']}/{axis['population']} 人）")
    return f"{target}{where} 最值得先看的指标是：" + "；".join(lines) + "。可查看指标对应的学生作答。"


def latest_clean(conn):
    runs = [item for item in all_of(conn, "job") if item["kind"] == "clean"]
    return max(runs, key=lambda item: item["created"]) if runs else None


def cleaning_card(conn, run):
    if run is None:
        return None, None
    snapshot = None
    stages = run.get("stages") or {}
    snapshot_id = (stages.get("python") or {}).get("snapshot_id") or (run.get("result") or {}).get("snapshot_id")
    if snapshot_id:
        snapshot = get(conn, "snapshot", snapshot_id)
    report = snapshot["report"] if snapshot else None
    card = {"kind": "cleaning", "run_id": run["id"], "batch_id": run["payload"].get("batch_id"),
            "state": run["state"], "stage": run.get("stage", ""), "label": run["payload"].get("label", ""),
            "error": run.get("error", ""), "report": report,
            "snapshot_id": snapshot_id if run.get("stage") != "published" else None}
    proposal = None
    if run["state"] == "done" and run.get("stage") == "confirm":
        proposal = make_proposal(conn, "confirm_import", {
            "batch_id": run["payload"]["batch_id"], "run_id": run["id"],
            "snapshot_id": snapshot_id,
        }, title="核对并确认这批数据")
    return card, proposal


def make_proposal(conn, kind, payload, title=""):
    if kind not in SUPPORTED:
        raise ValueError("不支持的操作类型")
    existing = None
    for item in all_of(conn, "proposal"):
        if (item["kind"] == kind and item.get("status") == "pending"
                and {key: item["payload"].get(key) for key in payload} == payload):
            existing = item
            break
    if existing:
        return existing
    proposal = {"id": new_id(), "kind": kind, "payload": payload, "title": title,
                "status": "pending", "created": time.time(), "result": None}
    put(conn, "proposal", proposal["id"], proposal)
    return proposal


def material_version(document):
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def proposal_card(item):
    labels = {"confirm_import": "确认数据", "generate_recommendation": "生成学习建议",
              "publish_material": "发布教学资料", "retry_job": "重试任务",
              "correct_attempt": "修正作答异常", "revise_draft": "修订学习建议", "revise_material": "校订资料"}
    return {"kind": "proposal", "id": item["id"], "action": item["kind"],
            "title": item.get("title") or labels[item["kind"]],
            "status": item.get("status", "pending"), "payload": item.get("payload", {}),
            "result": item.get("result")}


def resolve_recommendation(data, snapshot_id, resolved, anchor_event=""):
    if resolved["candidates"]:
        return None, {"kind": "candidates", "intent": "generate_recommendation",
                      "options": resolved["candidates"], "prompt": "找到多个匹配对象，请选择一个后再生成建议。"}
    student, group, node = resolved["student"], resolved["group"], resolved["node"]
    if not student and not group:
        options = [{"kind": "group", "id": key, "label": key + " · 班级"} for key in resolved["groups"]]
        options += [{"kind": "student", "id": key, "label": key + " · " + data["模拟学生"][key]["模拟班级编号"]}
                    for key in resolved["students"][:12]]
        return None, {"kind": "candidates", "intent": "generate_recommendation", "options": options,
                      "prompt": "请选择要生成学习建议的学生或班级。"}
    if node and node not in data["测验节点"]:
        return None, {"kind": "candidates", "prompt": "找不到该测验，请从已有测验中选择。",
                      "options": [{"kind": "node", "id": key, "label": key} for key in resolved["nodes"]]}
    if group and group not in resolved["groups"]:
        return None, {"kind": "candidates", "prompt": "找不到该班级。", "options": []}
    if student and student not in data["模拟学生"]:
        return None, {"kind": "candidates", "prompt": "找不到该学生。", "options": []}
    if anchor_event:
        row = data["作答记录"].get(anchor_event)
        if (not row or row["评分状态"] != "已评分" or (student and row["学生编号"] != student)
                or (node and row["节点编号"] != node)):
            anchor_event = ""
    payload = {"student": student, "group": group, "node": node,
               "snapshot_id": snapshot_id, "anchor_event": anchor_event}
    return payload, None


def deterministic(conn, request, snapshot):
    """Answer data questions and create operation proposals without a model."""
    question = request.question.strip()
    data = table_data(snapshot) if snapshot else None
    context = {"student": request.student, "group": getattr(request, "group", ""), "node": request.node}
    resolved = resolve(data, question, context) if data else {"candidates": []}
    if re.search(r"导入|上传", question) and not re.search(r"进度|查看|状态|确认|复核|异常|结果", question):
        return {"answer": "打开数据导入后，可以选择 XLSX 工作簿或成组的 CSV，并查看预检结果。",
                "cards": [{"kind": "import"}]}
    if RECOMMEND.search(question):
        if not snapshot:
            return {"answer": "还没有确认的数据快照，请先导入并确认第一批数据。",
                    "cards": [{"kind": "import", "title": "导入第一批数据"}]}
        if CONFIRM.search(question) and CLEANING.search(question) and not RECOMMEND.search(question):
            run = latest_clean(conn)
            card, proposal = cleaning_card(conn, run)
            if proposal:
                return {"answer": "本批数据已完成清洗复核，可核对有效作答与异常摘要后确认。",
                        "cards": [card, proposal_card(proposal)]}
            if card:
                return {"answer": "当前没有等待确认的清洗批次。", "cards": [card]}
        payload, card = resolve_recommendation(data, snapshot["id"], resolved, request.anchor_event)
        if card:
            return {"answer": card.get("prompt", "需要先选择对象。"), "cards": [card]}
        proposal = make_proposal(conn, "generate_recommendation", payload,
                                 title="生成学习建议 · " + (payload["student"] or payload["group"] or "全部"))
        target = payload["student"] or payload["group"]
        details = "对象 " + target + (" · " + payload["node"] if payload["node"] else " · 全部测验")
        return {"answer": "已按当前数据快照准备建议任务：" + details + "。确认后会在对话中跟踪生成和终审。",
                "cards": [{"kind": "proposal", **proposal_card(proposal), "details": details}]}
    if CLEANING.search(question):
        run = latest_clean(conn)
        if run is None:
            return {"answer": "还没有清洗批次。可以前往数据导入选择文件，预检通过后开始清洗。",
                    "cards": [{"kind": "import"}]}
        card, proposal = cleaning_card(conn, run)
        answer = {"queued": "本批数据正在排队清洗。", "running": "本批数据正在清洗，可查看当前阶段和计数。",
                  "failed": "本批清洗失败：" + (run.get("error") or "请查看任务详情") + "。可重试或修正文件后重新导入。",
                  "done": "本批清洗已完成。"}.get(run["state"], "本批数据正在处理。")
        cards = [card] + ([proposal_card(proposal)] if proposal else [])
        return {"answer": answer, "cards": cards}
    if not snapshot and VIEW.search(question) and re.search(r"班级|我们班|学生|测验|学情", question):
        return {"answer": "请先导入并确认第一批数据。", "cards": [{"kind": "import"}]}
    if data and (resolved.get("student") or resolved.get("group")) and VIEW.search(question) and not EXPLAIN.search(question):
        if resolved["candidates"]:
            return {"answer": "找到多个匹配对象，请选择一个。", "cards": [
                {"kind": "candidates", "intent": "view", "options": resolved["candidates"], "prompt": "请选择对象"}]}
        student, group = resolved["student"], resolved["group"]
        if student or group:
            card = profile_card(conn, data, snapshot["id"], student, group, resolved["node"], resolved["item"])
            return {"answer": summary_answer(card, question), "cards": [card]}
    return None


def save_session(conn, session_id, request, result):
    now = time.time()
    session = get(conn, "chat_session", session_id) if session_id else None
    if session is None:
        session = {"id": new_id(), "created": now, "turns": [],
                   "context": {"student": "", "group": "", "node": ""}}
    context = {"student": request.student, "group": request.group, "node": request.node}
    for card in result.get("cards", []):
        if card.get("kind") == "progress":
            context = {"student": card["student"], "group": "", "node": ""}
        elif card.get("kind") == "profile" and card.get("target"):
            context = {"student": card["target"]["id"] if card["target"].get("kind") == "student" else "",
                       "group": card["target"]["id"] if card["target"].get("kind") == "group" else "",
                       "node": card.get("node", "")}
        elif card.get("kind") == "proposal" and card.get("action") == "generate_recommendation":
            payload = card.get("payload", {})
            context = {"student": payload.get("student", ""), "group": payload.get("group", ""),
                       "node": payload.get("node", "")}
    context["snapshot_id"] = result.get("snapshot_id", "")
    turn = {"question": request.question, "answer": result.get("answer", ""), "parts": result.get("parts", []),
            "trace": result.get("trace", []), "mode": result.get("mode"), "context": context,
            "cards": result.get("cards", []), "snapshot_id": result.get("snapshot_id", ""),
            "model": result.get("model", ""), "model_call": result.get("model_call"), "at": now}
    session["turns"] = (session.get("turns", []) + [turn])[-MAX_TURNS:]
    session["context"] = context
    if result.get("selection"):
        session["selection"] = result["selection"]
    if request.provider:
        session["provider"] = request.provider
    session["updated"] = now
    put(conn, "chat_session", session["id"], session)
    return session


def session_view(conn, session_id):
    session = get(conn, "chat_session", session_id)
    if session is None:
        return None
    cards = []
    for turn in session.get("turns", []):
        for card in turn.get("cards", []):
            if card.get("kind") == "proposal":
                fresh = get(conn, "proposal", card["id"])
                if fresh:
                    cards.append(proposal_card(fresh))
    for card in cards:
        for turn in reversed(session.get("turns", [])):
            for old in turn.get("cards", []):
                if old.get("kind") == "proposal" and old["id"] == card["id"]:
                    old.update(card)
                    break
    return session


def onboarding(conn):
    snapshot = current_snapshot(conn)
    preference = settings_get(conn, "onboarding", {}) or {}
    run = latest_clean(conn)
    drafts = [item for item in all_of(conn, "draft")
              if snapshot and item.get("snapshot_id") == snapshot["id"] and not item.get("approved")]
    approved = [item for item in all_of(conn, "draft")
                if snapshot and item.get("snapshot_id") == snapshot["id"] and item.get("approved")]
    viewed = settings_get(conn, "onboarding_viewed_answer", {}) or {}
    state, detail = "empty", {}
    if snapshot:
        if approved and not preference.get("reopened"):
            return {"state": "completed", "detail": {}, "dismissed": bool(preference.get("dismissed")),
                    "completed": True, "snapshot_id": snapshot["id"],
                    "model_configured": bool(provider_key(active_provider(conn)))}
        if drafts:
            state, detail = "review_suggestion", {"draft": max(drafts, key=lambda item: item.get("updated", 0))}
        elif viewed.get("snapshot_id") == snapshot["id"]:
            state, detail = "viewed_answer", viewed
        else:
            data = table_data(snapshot)
            groups = sorted({row["模拟班级编号"] for row in data["模拟学生"].values()})
            nodes = [row["节点编号"] for row in data["测验节点"].values()]
            state, detail = "confirmed", {"groups": groups, "nodes": nodes, "snapshot_id": snapshot["id"]}
    elif run and run["state"] in {"queued", "running"}:
        state, detail = "cleaning", {"run_id": run["id"]}
    elif run and run["state"] == "failed":
        state, detail = "failed", {"run_id": run["id"], "error": run.get("error", "")}
    elif run and run["state"] == "done" and run.get("stage") == "confirm":
        card, proposal = cleaning_card(conn, run)
        state, detail = "review", {"run_id": run["id"], "batch_id": run["payload"]["batch_id"],
                                   "report": card["report"] if card else None,
                                   "proposal": proposal_card(proposal) if proposal else None}
    elif run:
        state, detail = "cleaning", {"run_id": run["id"]}
    return {"state": state, "detail": detail, "dismissed": bool(preference.get("dismissed")),
            "completed": bool(preference.get("completed")), "snapshot_id": snapshot["id"] if snapshot else None,
            "model_configured": bool(provider_key(active_provider(conn)))}
