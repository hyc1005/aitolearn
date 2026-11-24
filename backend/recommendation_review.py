import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .cleaning_agent import Finding, structured_review
from .data import profile
from .model_transport import ModelRequestError, request_budget


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator_id: str
    evidence_ids: list[str] = Field(max_length=4)
    detail: str = Field(min_length=1, max_length=700)


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1600)
    claims: list[Claim] = Field(max_length=6)


class FinalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["pass", "revise"]
    checks: dict[str, Literal["pass", "revise"]]
    summary: str = Field(min_length=1, max_length=1600)
    findings: list[Finding] = Field(max_length=20)


def representative_events(ids, rows, anchor, limit=4):
    chosen = [anchor] if anchor in ids else []
    seen_nodes = {rows[key]["节点编号"] for key in chosen}
    bands = [[], [], []]
    for key in sorted(ids):
        if key != anchor:
            row = rows[key]
            rate = int(row["得分"]) / int(row["满分"])
            bands[0 if rate < .35 else 1 if rate < .75 else 2].append(key)
    while len(chosen) < limit and any(bands):
        for band in bands:
            if band and len(chosen) < limit:
                key = min(band, key=lambda item: rows[item]["节点编号"] in seen_nodes)
                band.remove(key)
                chosen.append(key)
                seen_nodes.add(rows[key]["节点编号"])
    return chosen


def interpret(tables, payload, session):
    learner = profile(tables, *(payload.get(key, "") for key in ("student", "group", "node")))
    axes = {axis["id"]: axis for axis in learner["axes"]}
    anchor = payload.get("anchor_event")
    candidates = {key: representative_events(axis["attempt_ids"], tables["作答记录"], anchor)
                  for key, axis in axes.items()}
    samples = {
        key: {field: tables["作答记录"][key].get(field) for field in ("材料编号", "得分", "满分", "评分点得分")}
        for events in candidates.values() for key in events
    }
    facts = {"axes": {key: {"score": axis["score"], "n": axis["n"], "coverage": axis["coverage"],
                           "status": axis["status"], "sample_ids": candidates[key]}
                      for key, axis in axes.items()}, "samples": samples, "anchor_event": anchor}
    instruction = (
        "解读数学学情。选择最多三条有代表性的指标。indicator_id 必须是 axes 的键。"
        "evidence_ids 必须逐字复制该指标 sample_ids 内的事件编号，不能填写题号。"
        "有有效作答时使用作答支持判断，样本不足只提出观察。单次失误不得推广为稳定困难。"
        "anchor_event 非空时至少一条结论引用该作答事件。无证据时 claims 留空，summary 说明需要补充什么。"
    )
    for attempt in range(2):
        review, model = structured_review(Interpretation, instruction, facts, f"{session}-interpret-{attempt}")
        covers_anchor = not anchor or any(anchor in claim.evidence_ids for claim in review.claims)
        if covers_anchor and all(claim.indicator_id in candidates
               and set(claim.evidence_ids) <= set(candidates[claim.indicator_id])
               and (claim.evidence_ids or not axes[claim.indicator_id]["n"]) for claim in review.claims):
            return {"summary": review.summary, "claims": [
                {"detail": claim.detail, "refs": [claim.indicator_id, *claim.evidence_ids]} for claim in review.claims
            ], "model": model, "input_version": payload["snapshot_id"]}
        instruction += " 上次输出引用有误。只使用同一指标内实际列出的事件编号。"
    raise ValueError("学情结论引用了不属于该指标的作答")


def preview_text(draft):
    lines = [draft.get("message", draft.get("follow_up", ""))]
    for exercise in draft["exercises"]:
        key = exercise["question_id"]
        lines.append(f'{key}  {draft["questions"][key]["题干或内容"]}\n提示：{exercise["hints"][0]}')
    if draft.get("checked_variant"):
        lines.append(draft["checked_variant"]["question"])
    return "\n\n".join(filter(None, lines))


def final_review(draft, session):
    sections = {"sample": "样本及作答是否支持学情判断",
                "source": "已登记原文是否支持教学说明",
                "exercise": "练习及变式题是否对应观察和范围",
                "student_text": "学生可见文本是否与证据一致"}
    evidence_fields = ("材料编号", "节点编号", "指标", "得分", "满分", "评分点得分", "解答过程", "学生最终作答")
    evidence = {row["id"]: {k: row[k] for k in evidence_fields if k in row}
                for row in draft["evidence"]}
    citations = {block["id"]: {"text": block["text"], "locator": block["locator"],
                               "version": block["material"]} for block in draft["citations"]}
    refs = set(sections) | evidence.keys() | citations.keys() | draft["questions"].keys()
    questions = {key: {k: row[k] for k in ("题干或内容", "参考答案", "评分点", "指标编号", "满分") if k in row}
                 for key, row in draft["questions"].items()}
    facts = {
        "checks": sections, "observation": draft["observation"], "uncertainty": draft["uncertainty"],
        "axes": [{k: axis[k] for k in ("id", "score", "n", "coverage", "status")}
                 for axis in draft["profile"]["axes"]],
        "evidence": evidence, "citations": citations, "exercises": draft["exercises"],
        "teaching_points": draft.get("teaching_points", []),
        "questions": questions, "variant": draft["checked_variant"],
        "student_text": preview_text(draft), "insufficient_materials": draft["insufficient_materials"],
        "allowed_refs": sorted(refs),
    }
    instruction = (
        "独立终审最终草稿，不改写草稿。逐项检查全部四个 checks。任一项需退回时 decision 为 revise，"
        "findings 中指出需要修正的原句、原因、必要依据。refs 必须逐字复制 allowed_refs 中的编号。"
        "对每条 teaching_points 查阅它自己的 citation_ids，对练习目的核查它自己的 evidence_ids。"
        "全部检查通过时 decision 为 pass，findings 返回空数组。"
        "发现材料缺失时，只有草稿也明确说明不足且未给出无依据讲解和练习才允许通过。"
    )
    try:
        if len(json.dumps(facts, ensure_ascii=False).encode()) > request_budget() - 4096:
            raise ModelRequestError("context")
        review, model = review_checks(facts, instruction, session)
    except ModelRequestError as error:
        if error.code != "context":
            raise
        review, model = review_in_groups(facts, instruction, session)
    return {**review.model_dump(), "model": model, "input_version": draft["revision"],
            "snapshot_id": draft.get("snapshot_id")}


def review_checks(facts, instruction, session):
    for attempt in range(2):
        review, model = structured_review(FinalReview, instruction, facts, f"{session}-{attempt}")
        expected = "revise" if "revise" in review.checks.values() else "pass"
        if (review.checks.keys() == facts["checks"].keys() and review.decision == expected
                and (expected != "revise" or review.findings)
                and all(set(item.refs) <= set(facts["allowed_refs"]) for item in review.findings)):
            return review, model
        instruction += " 上次输出与结构或依据不匹配。仅使用本组 checks 中的键和 allowed_refs 中的引用。"
    raise ValueError("终审意见缺少检查项或使用了无效引用")


def review_in_groups(facts, instruction, session):
    common = {k: facts[k] for k in ("observation", "uncertainty", "insufficient_materials")}
    groups = [
        ("sample", {"axes": facts["axes"], "evidence": facts["evidence"]}),
        ("student_text", {k: facts[k] for k in ("student_text", "variant", "questions", "evidence")}),
    ]
    for point in facts["teaching_points"] or [None]:
        ids = point["citation_ids"] if point else []
        groups.append(("source", {"teaching_points": [point] if point else [],
                                  "citations": {key: facts["citations"][key] for key in ids}}))
    for exercise in facts["exercises"] or [None]:
        question_id = exercise["question_id"] if exercise else None
        groups.append(("exercise", {
            "exercises": [exercise] if exercise else [], "variant": facts["variant"],
            "questions": {question_id: facts["questions"][question_id]} if question_id else {},
            "evidence": {key: facts["evidence"][key] for key in exercise.get("evidence_ids", [])} if exercise else {},
        }))
    checks, findings, summaries = {}, [], []
    for index, (check, data) in enumerate(groups):
        refs = {check}
        for key in ("evidence", "citations", "questions"):
            refs.update(data.get(key, {}))
        review, model = review_checks({**common, **data, "checks": {check: facts["checks"][check]},
                                       "allowed_refs": sorted(refs)},
                                      instruction + " 只审查本组 checks 列出的项目及其完整依据。",
                                      f"{session}-group-{index}")
        if checks.get(check) != "revise":
            checks[check] = review.checks[check]
        summaries.append(review.summary)
        findings.extend(review.findings)
    return FinalReview(decision="revise" if "revise" in checks.values() else "pass", checks=checks,
                       summary="\n".join(summaries)[:1600], findings=findings[:20]), model
