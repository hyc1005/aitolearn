import time

from .materials import process
from .search import encode
from .store import all_of, db, get, put
from .workflow import review_recommendation, run_cleaning, run_recommendation


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
                put(conn, "job", job["id"], job)
        if not pending:
            time.sleep(0.6)
            continue
        try:
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
                vectors = encode([c["text"] for c in doc["blocks"]])
                for chunk, vector in zip(doc["blocks"], vectors):
                    chunk["vector"] = vector
                doc["state"] = "published"
                with db() as conn:
                    put(conn, "material", doc["id"], doc)
                    if doc.get("replaces"):
                        prior = get(conn, "material", doc["replaces"])
                        put(conn, "material", prior["id"], {**prior, "state": "replaced"})
                job["result"] = {"material": doc["id"]}
            elif job["kind"] == "clean":
                run_cleaning(job)
            elif job["kind"] == "generate":
                run_recommendation(job)
            elif job["kind"] == "recommendation_review":
                review_recommendation(job, job["payload"]["draft"], job["payload"]["revision"])
            else:
                raise ValueError("未知任务类型")
            job["state"] = "done"
        except Exception as exc:
            job["state"] = "failed"
            if type(exc) in (ValueError, RuntimeError, FileNotFoundError):
                job["error"] = str(exc)[:220]
            else:
                code = getattr(exc, "status_code", None)
                job["error"] = {401: "模型密钥无效", 429: "模型调用达到限额"}.get(code, type(exc).__name__)
            job.setdefault("failures", []).append({"stage": job.get("stage"),
                                                   "error": job["error"], "at": time.time()})
        with db() as conn:
            put(conn, "job", job["id"], job)


if __name__ == "__main__":
    run()
