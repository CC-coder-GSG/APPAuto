from __future__ import annotations


def success(data=None, message: str = "ok") -> dict:
    return {"success": True, "message": message, "data": data}


def error(message: str, code: str = "error") -> dict:
    return {"success": False, "message": message, "code": code}
