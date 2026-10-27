"""Standalone read-only comparison: python -m scripts.compare_databases --help."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.database_compare.engine import compare_databases
from app.services.database_compare.reports import export_report


def inputs(path: Path) -> dict[str, Path]:
    if path.is_file():
        return {path.name.casefold(): path}
    if not path.is_dir():
        raise ValueError(f"路径不存在: {path}")
    files = {p.name.casefold(): p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".db", ".sqlite", ".sqlite3")}
    if not files:
        raise ValueError(f"目录没有 SQLite 文件: {path}")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读比较两个 SQLite 文件或数据库目录，结果完整保存在独立 SQLite 文件中")
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="新的结果 SQLite 路径，不覆盖现有文件")
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--export", choices=("json", "excel"))
    args = parser.parse_args(argv)
    try:
        a, b = inputs(args.old), inputs(args.new)
        if args.old.is_file() and args.new.is_file():
            pairs = [{"name": args.new.name, "old": args.old, "new": args.new}]
        else:
            pairs = [{"name": (b.get(k) or a[k]).name, "old": a.get(k), "new": b.get(k)} for k in sorted(set(a) | set(b))]
        rules = json.loads(args.rules.read_text(encoding="utf-8")) if args.rules else {}
        summary = compare_databases(pairs, args.output, rules)
        if args.export:
            exported = export_report(args.output, args.output.parent / "exports", summary, args.export)
            print("Report:", exported)
        print(json.dumps({k: v for k, v in summary.items() if k != "files"}, ensure_ascii=True, indent=2))
        return 0 if summary["status"] == "succeeded" else 2
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
