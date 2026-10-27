from __future__ import annotations

import base64
import json


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def typed(value) -> dict:
    """Lossless, type-sensitive representation (including complete BLOBs)."""
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii"), "length": len(value)}
    if isinstance(value, int):
        # Strings preserve SQLite int64 values in JavaScript clients.
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": repr(value), "hex": value.hex()}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    raise TypeError(f"Unsupported SQLite value: {type(value).__name__}")


def row_values(names: list[str], row) -> dict:
    return dict(zip(names, map(typed, row)))


def canonical(values) -> str:
    return dumps(list(values))


def display(value) -> str:
    if isinstance(value, dict) and "type" in value:
        if value["type"] == "null":
            return "NULL"
        if value["type"] == "blob":
            return f"BLOB ({value['length']} bytes; base64): {value['value']}"
        return f"{value['type']}: {value['value']}"
    return dumps(value)
