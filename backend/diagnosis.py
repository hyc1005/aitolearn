from collections import Counter, defaultdict

from .data import profile, student_scores


def scoped_profile(tables, payload):
    return profile(tables, *(payload.get(key, "") for key in ("student", "group", "node")),
                   include_question_scope=True)


def node_date(tables, node):
    return tables["测验节点"].get(node, {}).get("模拟测验日期") or ""


def distribution_summary(values):
    if not values:
        return None
    values = sorted(values)

    def quantile(fraction):
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return round(values[lower] + (position - lower) * (values[upper] - values[lower]), 1)

    return {"count": len(values), "low": values[0], "q1": quantile(.25), "median": quantile(.5),
            "q3": quantile(.75), "high": values[-1], "points": values if len(values) < 5 else []}


def overview(tables, payload):
    learner = scoped_profile(tables, payload)
    topics = {row["id"]: row["label"] for row in learner["topics"]}
    result = []
    for axis in learner["axes"]:
        identifier = axis["id"]
        course = tables["课程要求"].get(identifier, {})
        rows = [tables["作答记录"][key] for key in axis["attempt_ids"]]
        by_node = defaultdict(list)
        for row in rows:
            by_node[row["节点编号"]].append(row)
        trend = []
        if learner["student"]:
            for node, attempts in by_node.items():
                score, maximum, count = next(iter(student_scores(attempts).values()))
                trend.append({"node": node, "date": node_date(tables, node),
                              "score": round(100 * score / maximum, 1), "n": count})
            trend.sort(key=lambda entry: (not entry["date"], entry["date"], entry["node"]))
        result.append({
            **{key: axis[key] for key in ("id", "label", "score", "n", "coverage", "population", "status")},
            "topic": course.get("主题编号", ""),
            "topic_label": topics.get(course.get("主题编号"), ""),
            "nodes": len(by_node), "zero_count": sum(int(row["得分"]) == 0 for row in rows),
            "distribution": distribution_summary(axis["distribution"]) if not learner["student"] else None,
            "trend": trend,
            "questions": [{"id": key, "source": tables["题目与材料"][key].get("来源或资料文件", "")}
                          for key in axis["question_ids"]],
            "references": [course[key] for key in ("课标文件页／正文页", "教材正文页") if course.get(key)],
        })
    return {"snapshot_id": payload["snapshot_id"], "student": learner["student"], "group": learner["group"],
            "node": learner["node"], "indicators": result}


def zero_evidence(tables, payload, indicator, node, item, offset, limit, event=""):
    axes = scoped_profile(tables, payload)["axes"]
    selected = [axis for axis in axes if not indicator or axis["id"] == indicator]
    ids = {key for axis in selected for key in axis["attempt_ids"]}
    rows = [tables["作答记录"][key] for key in ids if int(tables["作答记录"][key]["得分"]) == 0]
    if event:
        row = next((row for row in rows if row["作答事件编号"] == event), None)
        return {**row, "题目": tables["题目与材料"][row["材料编号"]]} if row else None
    items = sorted({row["材料编号"] for row in rows})
    nodes = sorted({row["节点编号"] for row in rows})
    rows = [row for row in rows if (not node or row["节点编号"] == node) and (not item or row["材料编号"] == item)]
    rows.sort(key=lambda row: (not node_date(tables, row["节点编号"]), node_date(tables, row["节点编号"]),
                               row["节点编号"], row["材料编号"], row["作答事件编号"]))
    counts = Counter(row["节点编号"] for row in rows)
    return {
        "total": len(rows), "items": items, "nodes": nodes,
        "timeline": [{"node": key, "date": node_date(tables, key), "count": count} for key, count in counts.items()],
        "rows": [{"id": row["作答事件编号"], "item": row["材料编号"], "node": row["节点编号"],
                  "date": node_date(tables, row["节点编号"]), "score": int(row["得分"]), "maximum": int(row["满分"])}
                 for row in rows[offset:offset + limit]],
    }
