import logging
import time

from .materials import process
from .model_transport import model_failure, model_job
from .search import optional_encode
from .store import all_of, db, get, put
from .workflow import JobStopped, checkpoint, review_recommendation, run_cleaning, run_recommendation


def run():
    with db() as conn:
        for job in all_of(conn, "job"):
            if job["state"] == "running":
                put(conn, "job", job["id"], {**job, "state": "queued"})

    while True:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = sorted((j for j in all_of(conn, "job") if j["state"] == "queued"),
                             key=lambda j: j["created"])
            if pending:
                job = pending[0]
                job["state"] = "running"
                job["model_retrying"] = False
                put(conn, "job", job["id"], job)
        if not pending:
            time.sleep(0.6)
            continue
        token = model_job.set(job)
        try:
            if job["kind"] in {"extract", "publish"}:
                checkpoint(job, job["kind"])
            if job["kind"] == "extract":
                with db() as conn:
                    doc = get(conn, "material", job["payload"]["material"])
                doc["blocks"], doc["previews"] = process(doc)
                doc["state"] = "review"
                for block in doc["blocks"]:
                    block.update(material=doc["id"], title=doc["name"])
                with db() as conn:
                    put(conn, "material", doc["id"], doc)
                job["result"] = {"material": doc["id"]}
            elif job["kind"] == "publish":
                with db() as conn:
                    doc = get(conn, "material", job["payload"]["material"])
                if doc["state"] != "published":
                    vectors = optional_encode([c["text"] for c in doc["blocks"]])
                    for chunk, vector in zip(doc["blocks"], vectors):
                        chunk["vector"] = vector
                    with db() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if doc.get("replaces"):
                            prior = get(conn, "material", doc["replaces"])
                            if prior["state"] != "published":
                                raise ValueError("被替换的资料已更新，请从当前发布版本重新校订")
                            put(conn, "material", prior["id"], {**prior, "state": "replaced"})
                        put(conn, "material", doc["id"], {**doc, "state": "published"})
                job["result"] = {"material": doc["id"]}
            elif job["kind"] == "clean":
                run_cleaning(job)
            elif job["kind"] == "generate":
                run_recommendation(job)
            elif job["kind"] == "recommendation_review":
                review_recommendation(job, job["payload"]["draft"], job["payload"]["revision"])
            else:
                raise ValueError("未知任务类型")
            if job["kind"] in {"extract", "publish"}:
                checkpoint(job, job["kind"], job["result"])
            job["state"] = "done"
        except JobStopped:
            job["state"] = "stopped"
        except Exception as exc:
            job["state"] = "failed"
            if failure := model_failure(exc):
                job["error"] = str(failure)
                job["model_error"] = failure.code
            elif type(exc) in (ValueError, RuntimeError, FileNotFoundError):
                job["error"] = str(exc)[:220]
            else:
                code = getattr(exc, "status_code", None)
                job["error"] = {401: "模型密钥无效", 429: "模型调用达到限额"}.get(code, type(exc).__name__)
            job.setdefault("failures", []).append({"stage": job.get("stage"),
                                                   "error": job["error"], "at": time.time(),
                                                   "code": job.get("model_error"),
                                                   "detail": failure.detail if failure else "",
                                                   "request": job.get("last_model_request")})
            timing = job.get("stage_timings", {}).get(job.get("stage"))
            if timing:
                timing["elapsed_ms"] = max(0, round((time.time() - timing["started_at"]) * 1000))
            logging.warning("job_failure run=%s stage=%s code=%s detail=%s", job["id"],
                            job.get("stage"), job.get("model_error"), failure.detail if failure else type(exc).__name__)
        finally:
            model_job.reset(token)
            job["model_retrying"] = False
        with db() as conn:
            put(conn, "job", job["id"], job)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run()
