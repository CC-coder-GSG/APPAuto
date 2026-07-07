"""禅道任务命名规范（2026-07-08 领导要求：测试相关任务统一 [测试] 前缀）。"""
from __future__ import annotations

TEST_TASK_PREFIX = "[测试]"


def ensure_test_prefix(name: str | None) -> str:
    """给任务名加 [测试] 前缀；已带（含手输的全角/带空格变体）不重复加。"""
    text = str(name or "").strip()
    for existing in (TEST_TASK_PREFIX, "【测试】"):
        if text.startswith(existing):
            return TEST_TASK_PREFIX + text[len(existing):].lstrip()
    return TEST_TASK_PREFIX + text


__all__ = ["TEST_TASK_PREFIX", "ensure_test_prefix"]
