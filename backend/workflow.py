import time

from .agent import generate
from .cleaning_agent import review_import
from .recommendation_review import final_review, interpret
from .snapshots import RULE_VERSION, digest, run_data_cleaning, table_data
from .store import db, get, put


class JobStopped(Exception):
    pass


def checkpoint(job, stage, result=None):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if result is None and get(conn, "job_stop", job["id"]):
            raise JobStopped
        job["stage"] = stage
        timing = job.setdefault("stage_timings", {})
        if result is None:
            timing[stage] = {"started_at": time.time(), "attempt": timing.get(stage, {}).get("attempt", 0) + 1}
        elif stage in timing:
            timing[stage]["elapsed_ms"] = max(0, round((time.time() - timing[stage]["started_at"]) * 1000))
        if result is not None:
            job.setdefault("stages", {})[stage] = {**result, "recorded_at": time.time(), "run_id": job["id"]}
        put(conn, "job", job["id"], job)


def run_cleaning(job):
    payload = job["payload"]
    checkpoint(job, "python")
    with db() as conn:
        baseline = get(conn, "snapshot", payload["base_snapshot_id"]) if payload["base_snapshot_id"] else None
        batches = [get(conn, "import", key) for key in payload["batch_ids"]]
        candidate_id = digest([payload["batch_ids"], payload["base_snapshot_id"], RULE_VERSION])
        candidate = get(conn, "snapshot", candidate_id)
    if candidate is None:
        rows = [row for batch in batches for row in batch["rows"]]
        result = run_data_cleaning(rows)
        manifest = [{"id": batch["id"], "sources": [{k: v for k, v in source.items() if k != "path"}
                                                   for source in batch["sources"]],
                     "mapping": batch.get("mapping", {}), "revision_of": batch.get("revision_of")}
                    for batch in batches]
        candidate = {**result, "id": candidate_id, "batch_ids": payload["batch_ids"],
                     "base_snapshot_id": payload["base_snapshot_id"], "manifest": manifest}
        with db() as conn:
            put(conn, "snapshot", candidate_id, candidate)
    checkpoint(job, "python", {"snapshot_id": candidate_id, "rules_version": candidate["rules_version"],
                                "output_hash": candidate["output_hash"]})
    if batches[-1].get("correction"):
        if any(issue["source"] == batches[-1]["label"] for issue in candidate["report"]["issues"]):
            raise ValueError("修订后仍有异常，请调整字段后重新提交修订")
    checkpoint(job, "clean_review")
    if "clean_review" not in job["stages"]:
        review = review_import(candidate, baseline, job["id"])
        checkpoint(job, "clean_review", review)
    job["result"] = {"snapshot_id": candidate_id, "batch_id": payload["batch_id"]}
    job["stage"] = "confirm" if job["stages"]["clean_review"]["decision"] == "ready" else "revision"


def run_recommendation(job):
    payload = job["payload"]
    if not payload.get("snapshot_id"):
        raise ValueError("历史任务未登记数据快照，请重新发起")
    with db() as conn:
        snapshot = get(conn, "snapshot", payload["snapshot_id"])
    if snapshot is None:
        raise ValueError("任务缺少已确认快照，请重新发起")
    tables = table_data(snapshot)
    if "snapshot" not in job["stages"]:
        checkpoint(job, "snapshot", {"snapshot_id": snapshot["id"], "material_ids": payload["material_ids"]})
    if "interpret" not in job["stages"]:
        checkpoint(job, "interpret")
        checkpoint(job, "interpret", interpret(tables, payload, job["id"]))
    with db() as conn:
        draft = get(conn, "draft", job["id"])
    if draft is None:
        checkpoint(job, "generate")
        result = generate(tables, payload, job["id"], job["stages"]["interpret"])
        draft = {**result, **payload, "id": job["id"], "run_id": job["id"], "message": result["follow_up"],
                 "approved": False, "revision": 1, "review_state": "pending"}
        with db() as conn:
            put(conn, "draft", draft["id"], draft)
            put(conn, "draft_revision", f'{draft["id"]}:1', draft)
    if "generate" not in job["stages"]:
        checkpoint(job, "generate", {"model": draft["model"], "trace": draft["trace"], "draft_id": draft["id"],
                                     "draft_mode": draft.get("draft_mode"), "material_gap": draft.get("material_gap"),
                                     "variant_omission_reason": draft.get("variant_omission_reason")})
    review_recommendation(job, draft["id"], 1)


def review_recommendation(job, draft_id, revision):
    revision_id = f"{draft_id}:{revision}"
    with db() as conn:
        draft = get(conn, "draft_revision", revision_id)
        if draft is None:
            raise ValueError("建议修订不存在")
    checkpoint(job, "final_review")
    review = draft.get("final_review") or final_review(draft, job["id"] + "-review")
    finished = {**draft, "final_review": review, "review_state": review["decision"], "review_run_id": job["id"]}
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = get(conn, "draft", draft_id)
        current = latest["revision"] == revision
        if not draft.get("final_review"):
            put(conn, "draft_revision", revision_id, finished)
        if current and not latest.get("final_review"):
            put(conn, "draft", draft_id, finished)
    checkpoint(job, "final_review", review)
    job["result"] = {"draft": draft_id, "revision": revision}
    job["stage"] = ("confirm" if review["decision"] == "pass" else "revision") if current else "superseded"
