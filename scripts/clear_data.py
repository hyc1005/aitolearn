"""Clear business data and derived artifacts while keeping source and runtimes.

The target list comes from data_inventory.scan().  Nothing is removed unless it
is under the project root, and the project source, ori_data, .venv, node_modules
and the declared test/dependency folders are never removed.
"""
import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from data_inventory import KEEP_VAR, ROOT, inside_keep, scan

KEEP_TOP = {".git", ".gitignore", ".env", "HANDOFF.md", "README.md", "backend", "frontend",
            "goapi.txt", "node_modules", "ocr_worker", "ori_data", "requirements.txt",
            "requirements-keyword-win-py312.txt", "scripts", "tests", ".venv", "var"}
KEEP_VAR_TOP = KEEP_VAR | {"delivery"}


def safe(path: Path):
    resolved = path.resolve()
    return resolved == ROOT or ROOT in resolved.parents


def remove_file(path: Path):
    if not safe(path) or inside_keep(path):
        return False
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
        return True
    return False


def clear_database(path: Path):
    if not safe(path) or inside_keep(path):
        return {"path": str(path), "cleared": 0}
    count = 0
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
                count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                conn.execute("DELETE FROM records")
                conn.commit()
                conn.execute("VACUUM")
        finally:
            conn.close()
    except sqlite3.Error:
        count = -1
    for suffix in ("-wal", "-shm"):
        sibling = path.with_name(path.name + suffix)
        if sibling.exists():
            sibling.unlink(missing_ok=True)
    return {"path": str(path), "cleared": count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the cleanup")
    args = parser.parse_args()
    inventory = scan()
    report = {"apply": args.apply, "removed": [], "databases": [], "kept": sorted(KEEP_TOP),
              "kept_var": sorted(KEEP_VAR_TOP)}
    if args.apply:
        for item in inventory["databases"]:
            report["databases"].append(clear_database(Path(item["path"])))
        for item in inventory["logs"] + inventory["screenshots_recordings_and_exports"]:
            if remove_file(Path(item["path"])):
                report["removed"].append(item["path"])
        for item in inventory["review_directories"]:
            path = Path(item["path"])
            if safe(path) and not inside_keep(path):
                shutil.rmtree(path, ignore_errors=True)
                report["removed"].append(item["path"] + "/")
        delivery = ROOT / "var" / "delivery"
        delivery.mkdir(parents=True, exist_ok=True)
        (delivery / "cleanup-inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        (delivery / "cleanup-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
