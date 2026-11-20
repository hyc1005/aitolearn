import hashlib
import json
import time
from collections import Counter

from .data import TABLES, clean
from .store import all_of, get, new_id, put

RULE_VERSION = "1"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def run_data_cleaning(rows):
    """Produce a JSON-compatible snapshot and retain all source occurrences."""
    records = {}
    provenance = {}
    input_attempts = 0

    def source_order(row):
        return (row["source"], row["sheet"], row["line"], canonical(row["values"]), row.get("file_id", ""))

    for row in sorted(rows, key=source_order):
        input_attempts += row["table"] == "作答记录"
        key = digest([row["table"], row["values"]])
        records.setdefault(key, row)
        provenance.setdefault(key, []).append({
            field: row[field] for field in ("source", "sheet", "line", "file_id") if field in row
        })
    tables, report = clean(list(records.values()))
    report["input_attempts"] = input_attempts
    report["duplicate_attempts"] = report["input_attempts"] - report["raw_attempts"]
    report["issue_counts"] = dict(Counter(issue["reason"] for issue in report["issues"]))
    for index, issue in enumerate(report["issues"], 1):
        issue["id"] = f"I{index:04d}"
    tables["总分"] = list(tables["总分"].values())
    result = {"rules_version": RULE_VERSION, "tables": tables, "report": report,
              "provenance": provenance}
    return {**result, "output_hash": digest(result)}


def table_data(snapshot):
    if snapshot is None:
        return {name: {} for name in (*TABLES, "总分")}
    data = snapshot["tables"]
    return {**data, "总分": {(r["学生编号"], r.get("节点编号", "")): r for r in data["总分"]}}


def current_snapshot(conn):
    pointer = get(conn, "settings", "active_snapshot")
    if pointer:
        snapshot = get(conn, "snapshot", pointer["id"])
        return {**snapshot, "review_run_id": pointer["review_run_id"]}


def legacy_batches(conn):
    return [batch["id"] for batch in all_of(conn, "import") if batch.get("confirmed")]


def queue_job(conn, kind, payload):
    item = {"id": new_id(), "kind": kind, "payload": payload, "state": "queued",
            "created": time.time(), "stages": {}, "failures": []}
    put(conn, "job", item["id"], item)
    return item


def queue_cleaning(conn, batch, baseline=None):
    active = current_snapshot(conn)
    base_batches = active["batch_ids"] if active else legacy_batches(conn)
    batch_ids = [key for key in base_batches if key != batch["id"]] + [batch["id"]]
    put(conn, "import", batch["id"], batch)
    return queue_job(conn, "clean", {
        "batch_id": batch["id"], "base_snapshot_id": active["id"] if active else None,
        "batch_ids": batch_ids, "legacy_baseline": baseline if baseline is not None else base_batches,
        "label": batch["label"],
    })
