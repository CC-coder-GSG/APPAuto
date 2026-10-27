from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .result import ResultStore
from .schema import fingerprint, open_readonly, quote, read_schema
from .values import canonical, dumps, row_values

ENGINE_VERSION = "1.0.0"


class CompareCancelled(Exception):
    pass


class MatchError(ValueError):
    pass


def validate_rules(rules: dict | None) -> dict:
    rules = rules or {}
    if not isinstance(rules, dict) or set(rules) - {"schema_version", "strict_comparison", "tables"}:
        raise ValueError("首期规则只支持 schema_version、strict_comparison 和 tables，其他规则尚未实现")
    if rules.get("schema_version", 1) != 1 or rules.get("strict_comparison", True) is not True:
        raise ValueError("只支持版本 1 的严格比较规则")
    tables = rules.get("tables", {})
    if not isinstance(tables, dict):
        raise ValueError("tables 必须为对象")
    for name, rule in tables.items():
        if not isinstance(rule, dict) or set(rule) - {"match_mode", "key"}:
            raise ValueError(f"{name}: 不支持的表规则")
        mode, key = rule.get("match_mode", "auto"), rule.get("key", [])
        if mode not in ("auto", "key", "row_multiset"):
            raise ValueError(f"{name}: match_mode 无效")
        if not isinstance(key, list) or any(not isinstance(x, str) or not x for x in key) or len(key) != len(set(key)):
            raise ValueError(f"{name}: key 必须是不重复的字段名称数组")
        if (mode == "key" and not key) or (mode == "row_multiset" and key):
            raise ValueError(f"{name}: 匹配方式与 key 不一致")
    return {"schema_version": 1, "strict_comparison": True, "tables": tables}


def _column_names(table: dict | None) -> list[str]:
    return [c["name"] for c in table["columns"] if c["hidden"] != 1] if table else []


def _structural(store, database, old, new):
    for key in ("user_version", "application_id", "encoding"):
        if old["metadata"].get(key) != new["metadata"].get(key):
            store.diff(database, "", "metadata", "metadata_changed", property=key,
                       old=old["metadata"].get(key), new=new["metadata"].get(key))
    for name in sorted(set(old["tables"]) | set(new["tables"])):
        a, b = old["tables"].get(name), new["tables"].get(name)
        if a is None or b is None:
            store.diff(database, name, "structure", "table_added" if a is None else "table_removed", old=a, new=b)
            continue
        ac, bc = ({c["name"]: c for c in t["columns"]} for t in (a, b))
        for col in sorted(set(ac) | set(bc)):
            if col not in ac or col not in bc:
                store.diff(database, name, "structure", "column_added" if col not in ac else "column_removed",
                           field=col, old=ac.get(col), new=bc.get(col))
            else:
                for prop in ("position", "type", "not_null", "default", "pk_order", "hidden"):
                    if ac[col][prop] != bc[col][prop]:
                        store.diff(database, name, "structure", "column_changed", field=col,
                                   property=prop, old=ac[col][prop], new=bc[col][prop])
        for prop in ("indices", "foreign_keys", "table_type", "sql"):
            # Convert PRAGMA tuples to JSON arrays before comparing.
            if dumps(a[prop]) != dumps(b[prop]):
                store.diff(database, name, "structure", "definition_changed", property=prop,
                           old=a[prop], new=b[prop], review_required=prop == "sql")
    ao, bo = ({(o["type"], o["name"]): o for o in s["objects"] if o["type"] in ("view", "trigger", "index")}
              for s in (old, new))
    for key in sorted(set(ao) | set(bo)):
        if ao.get(key) != bo.get(key):
            obj = bo.get(key) or ao[key]
            store.diff(database, obj["table"], "structure", "object_added" if key not in ao else
                       "object_removed" if key not in bo else "object_changed",
                       object_type=key[0], object_name=key[1], old=ao.get(key), new=bo.get(key))


def _stage(store, connections, name, tables, key_names, common, checkpoint):
    store.db.execute("DELETE FROM staged")
    counts = [0, 0]
    for side, (connection, table) in enumerate(zip(connections, tables)):
        if table is None:
            continue
        names = _column_names(table)
        cursor = connection.execute(f"SELECT {','.join(map(quote, names))} FROM {quote(name)}")
        for row in cursor:
            if counts[side] % 256 == 0:
                checkpoint()
            data = row_values(names, row)
            if key_names:
                if any(data[k]["type"] == "null" for k in key_names):
                    raise MatchError("匹配键包含 NULL")
                key = canonical(data[k] for k in key_names)
            else:
                key = canonical(data[k] for k in common)
            try:
                if key_names:
                    store.db.execute("INSERT INTO staged VALUES(?,?,?,1)", (side, key, dumps(data)))
                else:
                    store.db.execute("INSERT INTO staged VALUES(?,?,?,1) ON CONFLICT(side,key) DO UPDATE SET n=n+1",
                                     (side, key, dumps(data)))
            except sqlite3.IntegrityError as exc:
                raise MatchError("匹配键存在重复值") from exc
            if set(names) != set(common) and not key_names:
                store.diff(store.current_database, name, "evidence", "side_row", side="old" if side == 0 else "new", row=data)
            counts[side] += 1
    checkpoint()
    return counts


def _data(store, database, name, connections, tables, rule, checkpoint):
    a, b = tables
    names_a, names_b = map(_column_names, tables)
    common = [x for x in names_a if x in names_b] if a and b else (names_a or names_b)
    notes = []
    if any(t and t["table_type"] != "table" for t in tables):
        raise ValueError("虚拟表或影子表的数据比较尚未支持；对象定义已保留")
    if not common:
        common = []
        notes.append("两侧没有共同字段，已保存两侧完整数据；无法确认记录对应关系")
    mode, key = rule.get("match_mode", "auto"), rule.get("key", [])
    explicit = bool(key) or mode == "key"
    if not key and mode != "row_multiset" and a and b:
        keys = [[c["name"] for c in sorted(t["columns"], key=lambda c: c["pk_order"]) if c["pk_order"]] for t in tables]
        decl = [{c["name"]: c["type"] for c in t["columns"]} for t in tables]
        if keys[0] and keys[0] == keys[1] and all(decl[0][k] == decl[1][k] for k in keys[0]):
            key = keys[0]
    if key and any(k not in common for k in key):
        raise MatchError("指定匹配键在两侧不存在或不兼容")
    if not a or not b:
        key = []
    # Stage locally on disk. Savepoints prevent fallback from retaining stale evidence.
    store.db.execute("SAVEPOINT staging")
    try:
        counts = _stage(store, connections, name, tables, key, common, checkpoint)
        store.db.execute("RELEASE staging")
    except MatchError as exc:
        store.db.execute("ROLLBACK TO staging")
        store.db.execute("RELEASE staging")
        if explicit:
            raise
        notes.append(f"声明主键不可靠（{exc}），改用整行内容与次数比较")
        key = []
        counts = _stage(store, connections, name, tables, key, common, checkpoint)
    result = {"internal": (a or b)["internal"], "status": "compared", "old_rows": counts[0], "new_rows": counts[1],
              "match_mode": "key" if key else "row_multiset", "key": key, "notes": notes,
              "rows_added": 0, "rows_removed": 0, "rows_modified": 0, "fields_modified": 0}
    incomplete = bool(a and b and not key and set(names_a) != set(names_b))
    if incomplete:
        notes.append("无可靠匹配键且字段不一致：仅共同字段按次数比较，两侧完整行已保存在 evidence 明细中")
        result["status"] = "partial"
    if not key:
        notes.append("按内容及次数比较，不推断修改关系；物理行顺序不参与比较")
    rows = store.db.execute("""
        SELECT a.key,a.data,a.n,b.data,b.n FROM staged a
        LEFT JOIN staged b ON b.side=1 AND a.key=b.key WHERE a.side=0
        UNION ALL
        SELECT b.key,NULL,0,b.data,b.n FROM staged b
        LEFT JOIN staged a ON a.side=0 AND a.key=b.key WHERE b.side=1 AND a.key IS NULL
    """)
    for i, (token, old_json, old_n, new_json, new_n) in enumerate(rows):
        if i % 256 == 0:
            checkpoint()
        old, new = json.loads(old_json) if old_json else None, json.loads(new_json) if new_json else None
        if not key:
            delta = (new_n or 0) - (old_n or 0)
            if delta:
                kind = "row_added" if delta > 0 else "row_removed"
                result["rows_added" if delta > 0 else "rows_removed"] += abs(delta)
                store.diff(database, name, "data", kind, match_mode="row_multiset", count=abs(delta),
                           old_count=old_n or 0, new_count=new_n or 0, columns=common,
                           row={k: (new or old)[k] for k in common})
        elif old is None or new is None:
            kind = "row_added" if old is None else "row_removed"
            result["rows_added" if old is None else "rows_removed"] += 1
            store.diff(database, name, "data", kind, match_mode="key", key=json.loads(token), count=1, old=old, new=new)
        else:
            modified = False
            for field in sorted(set(old) | set(new)):
                if old.get(field) != new.get(field):
                    kind = "value_added" if field not in old else "value_removed" if field not in new else "value_changed"
                    store.diff(database, name, "data", kind, match_mode="key", key=dict(zip(key, json.loads(token))),
                               field=field, old=old.get(field), new=new.get(field))
                    if kind == "value_changed":
                        modified = True
                        result["fields_modified"] += 1
            result["rows_modified"] += int(modified)
    store.db.execute("DELETE FROM staged")
    return result


def compare_databases(pairs: list[dict], output: Path, rules: dict | None = None,
                      progress: Callable[[dict], None] | None = None,
                      cancelled: Callable[[], bool] | None = None) -> dict:
    """Compare named old/new file pairs, preserving full evidence in a result SQLite.

    Pairs: [{"name": "SYSTEM.db", "old": Path|None, "new": Path|None}].
    No source writes, arbitrary SQL, web dependencies or ORM schema assumptions.
    """
    rules = validate_rules(rules)
    if not pairs or len({p["name"] for p in pairs}) != len(pairs):
        raise ValueError("需要至少一组且名称不重复的数据库对应关系")
    output = Path(output).resolve()
    for pair in pairs:
        if not pair.get("old") and not pair.get("new"):
            raise ValueError("数据库对应关系两侧不能同时为空")
        if any(Path(pair[s]).resolve() == output for s in ("old", "new") if pair.get(s)):
            raise ValueError("结果文件不能覆盖输入数据库")
    summary = {"engine_version": ENGINE_VERSION, "started_at": datetime.now(timezone.utc).isoformat(),
               "status": "running", "conclusion": "unknown", "files": [], "errors": [],
               "tables_completed": 0, "tables_total": 0, "rules": rules, "limitations": []}
    store = ResultStore(output)
    connections = []
    snapshots = []
    manifests = []

    def checkpoint():
        if cancelled and cancelled():
            raise CompareCancelled("比较已取消或超过执行时限")

    def notify():
        checkpoint()
        if progress:
            progress({k: summary[k] for k in ("tables_completed", "tables_total", "status")})

    try:
        for pair in pairs:
            checkpoint()
            file_info, schemas, conns = {"name": pair["name"]}, [], []
            try:
                for side in ("old", "new"):
                    checkpoint()
                    if not pair.get(side):
                        schemas.append({"metadata": {}, "tables": {}, "objects": []})
                        conns.append(None)
                        file_info[side] = None
                        continue
                    path = Path(pair[side]).resolve()
                    before = fingerprint(path)
                    conn = open_readonly(path)
                    connections.append(conn)
                    conn.set_progress_handler(lambda: int(bool(cancelled and cancelled())), 10000)
                    schema = read_schema(conn)
                    schemas.append(schema)
                    conns.append(conn)
                    snapshots.append((path, before))
                    file_info[side] = {**before, "metadata": schema["metadata"], "objects": schema["objects"],
                                       "tables": schema["tables"]}
                manifests.append((pair, schemas, conns))
                summary["tables_total"] += len(set(schemas[0]["tables"]) | set(schemas[1]["tables"]))
            except (ValueError, OSError, sqlite3.Error) as exc:
                checkpoint()
                summary["errors"].append({"database": pair["name"], "error": str(exc)})
                store.diff(pair["name"], "", "coverage", "file_error", error=str(exc))
            summary["files"].append(file_info)
        known_tables = {f"{p['name']}.{name}" for p, schemas, _ in manifests for schema in schemas for name in schema["tables"]}
        unknown = set(rules["tables"]) - known_tables
        if unknown:
            summary["errors"].append({"error": "规则引用不存在的表: " + ", ".join(sorted(unknown))})
        for pair, schemas, conns in manifests:
            database = pair["name"]
            store.current_database = database
            if not pair.get("old") or not pair.get("new"):
                store.diff(database, "", "file", "database_added" if not pair.get("old") else "database_removed")
            _structural(store, database, *schemas)
            store.db.commit()
            for name in sorted(set(schemas[0]["tables"]) | set(schemas[1]["tables"])):
                notify()
                tables = [s["tables"].get(name) for s in schemas]
                store.db.execute("SAVEPOINT table_comparison")
                try:
                    result = _data(store, database, name, conns, tables, rules["tables"].get(f"{database}.{name}", {}), checkpoint)
                    store.db.execute("RELEASE table_comparison")
                except (ValueError, sqlite3.Error) as exc:
                    store.db.execute("ROLLBACK TO table_comparison")
                    store.db.execute("RELEASE table_comparison")
                    checkpoint()
                    result = {"status": "failed", "internal": (tables[0] or tables[1])["internal"], "error": str(exc)}
                    summary["errors"].append({"database": database, "table": name, "error": str(exc)})
                    store.diff(database, name, "coverage", "table_error", error=str(exc))
                except CompareCancelled:
                    store.db.execute("ROLLBACK TO table_comparison")
                    store.db.execute("RELEASE table_comparison")
                    store.table(database, name, {"status": "cancelled", "internal": (tables[0] or tables[1])["internal"]})
                    raise
                store.table(database, name, result)
                if result["status"] == "partial":
                    summary["limitations"].append({"database": database, "table": name, "notes": result["notes"]})
                summary["tables_completed"] += 1
                store.db.commit()
                notify()
        for path, before in snapshots:
            checkpoint()
            if fingerprint(path) != before:
                summary["errors"].append({"database": path.name, "error": "输入在比较期间发生变化，结果不能用于一致性判定"})
        incomplete = bool(summary["errors"] or summary["limitations"])
        summary["status"] = ("partial" if manifests else "failed") if incomplete else "succeeded"
    except CompareCancelled as exc:
        summary["status"] = "cancelled"
        summary["errors"].append({"error": str(exc)})
    except Exception as exc:
        summary["status"] = "failed"
        summary["errors"].append({"error": str(exc)})
    finally:
        for conn in connections:
            conn.close()
        summary["counts"] = {scope: n for scope, n in store.db.execute("SELECT scope,COUNT(*) FROM differences GROUP BY scope")}
        totals = {k: 0 for k in ("rows_added", "rows_removed", "rows_modified", "fields_modified")}
        for (raw,) in store.db.execute("SELECT data_json FROM table_results"):
            row = json.loads(raw)
            for key in totals:
                totals[key] += row.get(key, 0)
        summary.update(totals)
        has_changes = any(summary["counts"].get(s, 0) for s in ("structure", "data", "file", "metadata"))
        summary["has_changes"] = bool(has_changes)
        summary["conclusion"] = ("different" if has_changes else "equal") if summary["status"] == "succeeded" else (
            "different_in_covered_scope" if has_changes else "inconclusive")
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        store.meta("summary", summary)
        store.close()
    return summary
