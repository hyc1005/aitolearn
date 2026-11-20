from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .cleaning_agent import Finding, structured_review
from .data import profile


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


def interpret(tables, payload, session):
    learner = profile(tables, *(payload.get(key, "") for key in ("student", "group", "node")))
    axes = {axis["id"]: axis for axis in learner["axes"]}
    anchor = payload.get("anchor_event")
    candidates = {key: sorted(axis["attempt_ids"], key=lambda event: (
        event != anchor, int(tables["作答记录"][event]["得分"]) / int(tables["作答记录"][event]["满分"]), event
    ))[:4] for key, axis in axes.items()}
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
    evidence = {row["id"]: {k: v for k, v in row.items() if k not in ("来源", "学生编号")}
                for row in draft["evidence"]}
    citations = {block["id"]: {"text": block["text"], "locator": block["locator"],
                               "version": block["material"]} for block in draft["citations"]}
    refs = set(sections) | evidence.keys() | citations.keys() | draft["questions"].keys()
    facts = {
        "checks": sections, "observation": draft["observation"], "uncertainty": draft["uncertainty"],
        "axes": [{k: axis[k] for k in ("id", "score", "n", "coverage", "status")}
                 for axis in draft["profile"]["axes"]],
        "evidence": evidence, "citations": citations, "exercises": draft["exercises"],
        "teaching_points": draft.get("teaching_points", []),
        "questions": draft["questions"], "variant": draft["checked_variant"],
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
    for attempt in range(2):
        review, model = structured_review(FinalReview, instruction, facts, f"{session}-{attempt}")
        expected = "revise" if "revise" in review.checks.values() else "pass"
        if (review.checks.keys() == sections.keys() and review.decision == expected
                and (expected != "revise" or review.findings)
                and all(set(item.refs) <= refs for item in review.findings)):
            return {**review.model_dump(), "model": model, "input_version": draft["revision"],
                    "snapshot_id": draft.get("snapshot_id")}
        instruction += " 上次输出与结构或依据不匹配。仅使用 checks 中的四个键和 allowed_refs 中的引用。"
    raise ValueError("终审意见缺少检查项或使用了无效引用")
