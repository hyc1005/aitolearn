"""Run the same deterministic Python cleaner used by the workbench."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.data import read_file
from backend.snapshots import run_data_cleaning


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, help="JSON file mapping input columns to standard names")
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding="utf-8-sig")) if args.mapping else {}
    if not isinstance(mapping, dict) or not all(isinstance(value, str) for value in mapping.values()):
        parser.error("column mapping must be a JSON object with string values")
    files = sorted(args.input.glob("*.csv")) if args.input.is_dir() else [args.input]
    if not files:
        parser.error("no CSV inputs found")
    rows, sources = [], []
    for source in files:
        raw = source.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        try:
            parsed = read_file(source.name, raw, mapping)
        except ValueError as error:
            if args.input.is_dir() and str(error) == "未识别到 CSV 数据，请检查列映射":
                print(f"Skipped CSV with unrecognized headers: {source.name}")
                continue
            raise
        rows.extend({**row, "file_id": fingerprint} for row in parsed)
        sources.append({"name": source.name, "hash": fingerprint})
    if not rows:
        parser.error("no recognized data rows found")
    result = run_data_cleaning(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {"tables": result["tables"], "report": result["report"],
               "issues": result["report"]["issues"], "provenance": result["provenance"],
               "manifest": {"inputs": sources, "mapping": mapping,
                            "rules_version": result["rules_version"], "output_hash": result["output_hash"]}}
    for name, value in outputs.items():
        (args.output / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["output_hash"])


if __name__ == "__main__":
    main()
