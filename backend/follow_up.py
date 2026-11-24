from datetime import date
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .data import split
from .student_query import measure, metadata, observed_nodes
from .store import all_of, db, get, put


router = APIRouter(prefix="/api")
KIND = "recommendation_follow_up"


class Assignment(BaseModel):
    revision: int = Field(ge=1)
    baseline_node: str
    directions: list[str] = Field(min_length=1)


def measurement_date(node):
    try:
        return date.fromisoformat(node.get("模拟测验日期", ""))
    except (ValueError, TypeError):
        return None


def options(conn, draft, tables):
    directions = {}
    for exercise in draft["exercises"]:
        key = exercise["question_id"]
        question = tables["题目与材料"].get(key, {})
        for indicator in split(question.get("指标编号", "")):
            course = tables["课程要求"].get(indicator, {})
            entry = directions.setdefault("indicator:" + indicator, {
                "kind": "indicator", "value": indicator, "label": course.get("观察指标") or indicator,
                "questions": [], "topics": [],
            })
            entry["questions"].append(key)
        form, topic = question.get("题型"), question.get("主题编号")
        if form and topic:
            entry = directions.setdefault("form:" + form, {
                "kind": "form", "value": form, "label": form, "questions": [], "topics": [],
            })
            entry["questions"].append(key)
            if topic not in entry["topics"]:
                entry["topics"].append(topic)
    observed = observed_nodes(conn, draft["snapshot_id"], draft["student"])
    nodes = [{"id": key, "date": when.isoformat()} for key, node in tables["测验节点"].items()
             if key in observed and (when := measurement_date(node))]
    nodes.sort(key=lambda node: (node["date"], node["id"]), reverse=True)
    scored_nodes = observed_nodes(conn, draft["snapshot_id"], draft["student"], scored_only=True)
    default = next((node["id"] for node in nodes if node["id"] == draft.get("node")),
                   next((node["id"] for node in nodes if node["id"] in scored_nodes), ""))
    return {"directions": [{"id": key, **value} for key, value in directions.items()],
            "nodes": nodes, "default_node": default}


def filters(direction):
    if direction['kind'] == 'indicator':
        return {'skill': direction['value']}
    return {'form': direction['value'], 'topics': direction['topics']}


def results(conn, item):
    base = metadata(conn, item["snapshot_id"])
    sid = (get(conn, "settings", "active_snapshot") or {}).get("id", "")
    tables = metadata(conn, sid)
    student = item["student"]
    baseline_node = item["baseline_node"]
    when = measurement_date(base["测验节点"].get(baseline_node, {}))
    observed = observed_nodes(conn, sid, student)
    activity = observed_nodes(conn, sid)
    new_activity = activity - set(item.get("active_nodes", []))
    new_nodes = (observed | new_activity | (tables["测验节点"].keys() - set(item["known_nodes"])))
    new_nodes -= set(item["observed_nodes"])
    new_nodes.discard(baseline_node)
    nodes = [(measurement_date(tables["测验节点"].get(key, {})), key) for key in new_nodes]
    later = sorted((day, key) for day, key in nodes if day and when and day > when)
    return {**item, "active_snapshot_id": sid,
            "undated_nodes": sorted(key for day, key in nodes if day is None or day == when),
            "results": [{"direction": direction,
                         "baseline": measure(conn, item["snapshot_id"], student, baseline_node, **filters(direction)),
                         "observations": [measure(conn, sid, student, key, **filters(direction)) for _, key in later]}
                        for direction in item["directions"]]}


@router.get("/recommendations/{key}/follow-up")
def read_assignment(key: str, revision: int):
    with db() as conn:
        draft = get(conn, "draft_revision", f"{key}:{revision}")
        if not draft or not draft.get("student") or not draft.get("snapshot_id"):
            raise HTTPException(404, "没有可跟踪的学生建议")
        item = get(conn, KIND, f"{key}:{revision}")
        return {**options(conn, draft, metadata(conn, draft["snapshot_id"])), "assignment": results(conn, item) if item else None}


@router.put("/recommendations/{key}/follow-up")
def assign(key: str, selection: Assignment):
    record_id = f"{key}:{selection.revision}"
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = get(conn, KIND, record_id)
        if prior:
            if (prior["baseline_node"] != selection.baseline_node
                    or {item["id"] for item in prior["directions"]} != set(selection.directions)):
                raise HTTPException(409, "本版建议已登记布置")
            return results(conn, prior)
        draft = get(conn, "draft", key)
        if not draft or draft["revision"] != selection.revision or not draft["approved"]:
            raise HTTPException(409, "请先确认当前版本的学习建议")
        if not draft.get("student") or not draft.get("snapshot_id"):
            raise HTTPException(422, "只有已登记快照的学生建议可跟踪")
        base = metadata(conn, draft["snapshot_id"])
        available = options(conn, draft, base)
        directions = [item for item in available["directions"] if item["id"] in selection.directions]
        if (len(directions) != len(selection.directions)
                or selection.baseline_node not in {node["id"] for node in available["nodes"]}):
            raise HTTPException(422, "请选择该建议的练习方向和有效基线测验")
        sid = (get(conn, "settings", "active_snapshot") or {}).get("id", "")
        tables = metadata(conn, sid)
        item = {"draft_id": key, "revision": selection.revision, "student": draft["student"],
                "snapshot_id": draft["snapshot_id"], "baseline_node": selection.baseline_node,
                "assignment_snapshot_id": sid, "assigned_at": time.time(),
                "directions": directions, "known_nodes": sorted(tables["测验节点"]),
                "active_nodes": sorted(observed_nodes(conn, sid)),
                "observed_nodes": sorted(observed_nodes(conn, sid, draft["student"]))}
        put(conn, KIND, record_id, item)
        return results(conn, item)


@router.get("/students/{student}/follow-ups")
def student_assignments(student: str):
    with db() as conn:
        items = [item for item in all_of(conn, KIND) if item["student"] == student]
        return [results(conn, item) for item in sorted(items, key=lambda item: item["assigned_at"], reverse=True)]
