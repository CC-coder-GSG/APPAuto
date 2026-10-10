"""Hide captured user identifiers when presenting both new and historical reports."""
from __future__ import annotations

import json
import re
import unicodedata


_LABEL = r'''["']?(?:用户[ \t_-]*[iｉ][dｄ]|user[ \t_-]*id|uid)["']?'''
_LINE = re.compile(rf"^[ \t]*{_LABEL}[ \t]*(?:(?:[:=：]|[ \t]+)[ \t]*(?P<value>.*))?$", re.I)
_INLINE = re.compile(
    rf'''(?<![\w]){_LABEL}[ \t]*[:=：\t][ \t]*(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;，；|}}\]<>]+)''',
    re.I,
)
_OTHER_FIELD = re.compile(r"[^\s:=：]{1,80}[ \t]*[:=：]")
_FIELD_LABELS = {"版本", "应用版本", "系统版本", "设备", "设备机型", "异常上报时间", "发生时间",
                 "堆栈", "网络类型", "前后台状态", "reportid", "issueid", "device", "stack", "version"}
_OMIT = object()


def is_user_id_field(key):
    key = unicodedata.normalize("NFKC", str(key)).casefold()
    return re.sub(r'''[\s_\-:："']''', "", key) in {"用户id", "userid", "uid"}


def _remember(values, value):
    value = str(value).strip().rstrip(",;")
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except ValueError:
            value = value.strip('"')
    elif value.startswith("'"):
        value = value.strip("'")
    if isinstance(value, (str, int, float)) and str(value).strip():
        values.add(str(value).strip())


def _user_field_record(value):
    return (isinstance(value, dict) and any(key in value for key in ("value", "text", "content"))
            and any(is_user_id_field(value.get(key, "")) for key in ("label", "name", "key", "field")))


def _json_document(text):
    if text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            return None
        if isinstance(value, (dict, list)):
            return value
    return None


def _hide_labeled_text(text, values):
    lines = text.splitlines(keepends=True)
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _LINE.fullmatch(line.rstrip("\r\n"))
        if match:
            value = match["value"] or ""
            # Several fields can share a line; keep the neighbouring fields.
            if value and not _OTHER_FIELD.search(value):
                _remember(values, value)
                index += 1
                continue
            if not value:
                index += 1
                # Some Bugly captures put a field label and its value on separate lines.
                following_index = index
                while following_index < len(lines) and not lines[following_index].strip():
                    following_index += 1
                if following_index < len(lines):
                    following = lines[following_index].strip()
                    if (following and not _OTHER_FIELD.search(following)
                            and not is_user_id_field(following)
                            and following.casefold() not in _FIELD_LABELS
                            and re.fullmatch(r"[\w.@+\-]+", following)):
                        _remember(values, following)
                        index = following_index + 1
                continue

        def hide(match):
            _remember(values, match["value"])
            return ""

        result.append(_INLINE.sub(hide, line))
        index += 1
    return "".join(result)


def user_id_values(data):
    values = set()

    def collect(value):
        if isinstance(value, dict):
            if _user_field_record(value):
                for key in ("value", "text", "content"):
                    if isinstance(value.get(key), (str, int, float)):
                        _remember(values, value[key])
            for key, item in value.items():
                if is_user_id_field(key) and isinstance(item, (str, int, float)):
                    _remember(values, item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            document = _json_document(value)
            if document is not None:
                collect(document)
            else:
                _hide_labeled_text(value, values)

    collect(data)
    return values


def hide_user_ids(data, known_values=()):
    """Return a sanitized copy; never modify stored captures or their import hashes."""
    values = user_id_values(data) | {str(value) for value in known_values if value is not None}
    # Match complete values, not pieces of versions, report numbers or stack frames.
    tokens = sorted((value for value in values if len(value) >= 3), key=len, reverse=True)
    pattern = re.compile(r"(?<![\w.-])(?:" + "|".join(map(re.escape, tokens)) + r")(?![\w.-])") if tokens else None

    def clean(value):
        if isinstance(value, dict):
            if _user_field_record(value):
                return _OMIT
            result = {}
            for key, item in value.items():
                if is_user_id_field(key):
                    continue
                item = item if key in {"reportId", "issueId"} else clean(item)
                if item is not _OMIT:
                    result[key] = item
            return result
        if isinstance(value, list):
            return [item for item in map(clean, value) if item is not _OMIT]
        if isinstance(value, str):
            document = _json_document(value)
            if document is not None:
                sanitized = clean(document)
                if sanitized is _OMIT:
                    return ""
                return value if sanitized == document else json.dumps(sanitized, ensure_ascii=False, indent=2)
            text = _hide_labeled_text(value, set())
            return pattern.sub("[已脱敏]", text) if pattern else text
        return value

    result = clean(data)
    return None if result is _OMIT else result
