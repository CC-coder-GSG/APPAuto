from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .values import row_values


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def open_readonly(path: Path) -> sqlite3.Connection:
    path = path.resolve(strict=True)
    # Immutable mode avoids journal writes. Require an offline, exported snapshot.
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"{path.name} 存在 {suffix}，请先停止写入并导出完整快照")
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError(f"{path.name} 不是可读取的 SQLite 3 文件（可能损坏或加密）")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.enable_load_extension(False)
    return connection


def read_schema(connection: sqlite3.Connection) -> dict:
    checks = [r[0] for r in connection.execute("PRAGMA quick_check")]
    if checks != ["ok"]:
        raise ValueError("数据库完整性检查失败: " + "; ".join(checks[:5]))
    objects = [dict(zip(("type", "name", "table", "sql"), row)) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    )]
    table_types = {r[1]: r[2] for r in connection.execute("PRAGMA table_list") if r[0] == "main"}
    tables = {}
    for obj in objects:
        if obj["type"] != "table":
            continue
        name = obj["name"]
        columns = [dict(zip(("position", "name", "type", "not_null", "default", "pk_order", "hidden"), r))
                   for r in connection.execute(f"PRAGMA table_xinfo({quote(name)})")]
        indices = []
        for item in connection.execute(f"PRAGMA index_list({quote(name)})"):
            indices.append({"name": item[1], "unique": item[2], "origin": item[3], "partial": item[4],
                            "columns": list(connection.execute(f"PRAGMA index_xinfo({quote(item[1])})"))})
        tables[name] = {"sql": obj["sql"], "columns": columns,
                        "indices": sorted(indices, key=lambda i: i["name"]),
                        "foreign_keys": list(connection.execute(f"PRAGMA foreign_key_list({quote(name)})")),
                        "table_type": table_types.get(name, "table"), "internal": name.startswith("sqlite_")}
    metadata = {key: connection.execute(f"PRAGMA {key}").fetchone()[0]
                for key in ("user_version", "application_id", "encoding")}
    # VERSIONDATA is evidence, not an inferred application version ordering.
    if "VERSIONDATA" in tables:
        names = [c["name"] for c in tables["VERSIONDATA"]["columns"] if c["hidden"] != 1]
        metadata["version_data"] = [row_values(names, r) for r in connection.execute(
            f"SELECT {','.join(map(quote, names))} FROM VERSIONDATA ORDER BY 1"
        )]
    return {"metadata": metadata, "tables": tables, "objects": objects}


def inspect_file(path: Path) -> dict:
    before = fingerprint(path)
    connection = open_readonly(path)
    try:
        schema = read_schema(connection)
        for name, table in schema["tables"].items():
            table["rows"] = (connection.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]
                             if table["table_type"] == "table" else None)
        if fingerprint(path) != before:
            raise ValueError("输入在读取期间发生变化，请重新导出快照")
        return {"file": before, **schema}
    finally:
        connection.close()
