"""List business data and derived artifacts without touching dependencies.

Run with --json for a machine-readable inventory.  The inventory never opens
goapi.txt or the system credential store, so key values cannot leak into logs.
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Runtime environments and local test/browser dependencies are deliberately kept.
KEEP = {
    ".venv", "node_modules", ".git", "ori_data", "backend", "frontend", "ocr_worker",
    "scripts", "tests",
}
KEEP_VAR = {"browser-test", "npm-cache", "test-deps", "ocr-deps", "ocr-packages", "delivery"}
ARTIFACT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".pdf", ".pptx",
                     ".docx", ".xlsx", ".csv", ".json", ".log", ".zip"}
DB_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db")


def inside_keep(path: Path):
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return True
    parts = relative.parts
    if not parts:
        return True
    if parts[0] in KEEP:
        return True
    if parts[0] == "var":
        if len(parts) == 1:
            return True
        if parts[1] in KEEP_VAR:
            return True
        if parts[1] == ".venv":
            return True
    return False


def size(path: Path):
    try:
        return path.stat().st_size if path.is_file() else sum(
            child.stat().st_size for child in path.rglob("*") if child.is_file())
    except OSError:
        return 0


def scan():
    databases, uploads, previews, artifacts, logs, review_dirs = [], [], [], [], [], []
    var = ROOT / "var"
    if var.is_dir():
        for child in sorted(var.iterdir()):
            if child.name in KEEP_VAR or child.name.startswith(".venv"):
                continue
            if child.is_dir():
                if (child / "aitolearn.sqlite3").exists() or child.name.endswith(("-review", "-check",
                        "-install", "-verified", "acceptance", "acceptance-new")) or child.name.startswith("acceptance") or child.name in {
                        "frontend-before-agent", "agent-v2-csv", "agent-v2-xlsx"}:
                    review_dirs.append(child)
    for path in ROOT.rglob("*"):
        if not path.is_file() or inside_keep(path):
            continue
        relative = path.relative_to(ROOT)
        if path.name.endswith((".sqlite3", ".db")):
            databases.append(path)
        if path.name.endswith((".sqlite3-wal", ".sqlite3-shm")) or path.suffix.lower() == ".log":
            logs.append(path)
        if path.suffix.lower() in ARTIFACT_SUFFIXES:
            artifacts.append(path)
        if "files" in relative.parts or "previews" in relative.parts:
            (previews if "previews" in relative.parts else uploads).append(path)
    # Review folders are reported as whole paths; their individual files are not repeated.
    review_set = set(review_dirs)
    def in_review(path):
        return any(review in path.parents for review in review_set)
    return {
        "root": str(ROOT),
        "databases": [{"path": str(path), "bytes": size(path)} for path in databases if not in_review(path)],
        "review_directories": [{"path": str(path), "bytes": size(path)} for path in review_dirs],
        "uploaded_and_derived_files": [{"path": str(path), "bytes": size(path)} for path in uploads if not in_review(path)],
        "preview_files": [{"path": str(path), "bytes": size(path)} for path in previews if not in_review(path)],
        "screenshots_recordings_and_exports": [{"path": str(path), "bytes": size(path)}
                                               for path in artifacts if not in_review(path)],
        "logs": [{"path": str(path), "bytes": size(path)} for path in logs if not in_review(path)],
    }


def database_counts(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
                return {}
            return {kind: count for kind, count in conn.execute(
                "SELECT kind, COUNT(*) FROM records GROUP BY kind ORDER BY kind")}
        finally:
            conn.close()
    except sqlite3.Error:
        return {"error": 1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--counts", action="store_true", help="read record counts from databases")
    args = parser.parse_args()
    data = scan()
    if args.counts:
        for item in data["databases"]:
            item["records"] = database_counts(Path(item["path"]))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for section, items in data.items():
            if section == "root":
                print("root", items)
                continue
            print(f"\n{section}: {len(items)}")
            for item in items:
                if isinstance(item, dict):
                    print(f"  {item['path']}  ({item['bytes']} bytes)")
                    if item.get("records"):
                        print("    records", json.dumps(item["records"], ensure_ascii=False))
