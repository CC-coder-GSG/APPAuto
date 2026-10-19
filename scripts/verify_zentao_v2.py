r"""Run migration regression tests with an isolated database and no network.

Usage: .venv\Scripts\python.exe scripts/verify_zentao_v2.py [pytest arguments]
This verifies local code only; it never establishes a real Zentao connection.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    output = root / ".codex-tmp" / ("zentao-v2-" + uuid4().hex[:12])
    output.mkdir(parents=True)
    # Explicit environment values override the application's production .env.
    os.environ.update({
        "APP_ENV": "dev",
        "APP_DATABASE_URL": "sqlite:///" + (output / "test.sqlite").as_posix(),
        "APP_SECRET_KEY": "zentao-v2-offline-regression-key",
        "APP_ZENTAO_BACKGROUND_SYNC_ENABLED": "false",
        "APP_ZENTAO_BUILD_SYSTEM_MAPPING_JSON": "",
        "APP_WECOM_WEBHOOK_URL": "",
        "WECHAT_WEBHOOK_URL": "",
        "APP_N8N_ZENTAO_AI_WEBHOOK_URL": "",
        "N8N_ZENTAO_AI_WEBHOOK_URL": "",
        "APP_JENKINS_DEFAULT_BASE_URL": "http://offline.invalid",
        "APP_TERMINAL_CONTROL_ENABLED": "false",
        "OMNIQA_OFFLINE_TESTS": "1",
    })

    def deny_network(*args, **kwargs):
        raise RuntimeError("Offline regression: real network access is disabled")

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    internal = threading.local()

    def local_socketpair(*args, **kwargs):
        # Windows asyncio implements its wakeup pipe using a loopback pair.
        internal.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            internal.socketpair = False

    def guarded_connect(sock, address):
        if (getattr(internal, "socketpair", False) and isinstance(address, tuple)
                and address[0] in ("127.0.0.1", "::1")):
            return original_connect(sock, address)
        return deny_network()

    socket.socketpair = local_socketpair
    socket.create_connection = deny_network
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = deny_network

    import pytest

    args = sys.argv[1:] or [
        "tests/test_zentao_v2_protocol.py",
        "tests/test_zentao_v2_tools.py",
        "tests/test_zentao_client_service.py", "tests/test_zentao_client_recover.py",
        "tests/test_zentao_ai_cases.py", "tests/test_zentao_bug_actions.py",
        "tests/test_zentao_task_lifecycle.py", "tests/test_zentao_task_sync.py",
        "tests/test_zentao_task_status.py", "tests/test_zentao_task_schedule.py",
        "tests/test_zentao_task_preview.py", "tests/test_zentao_efforts.py",
        "tests/test_zentao_effort_reconciliation.py",
        "tests/test_zentao_story_change.py", "tests/test_zentao_version_sync_rename.py",
        "tests/test_zentao_project_product_mapping.py", "tests/test_zentao_testcase_normalize.py",
        "tests/test_zentao_utils.py", "tests/test_task_workbench.py", "tests/test_task_board.py",
        "tests/test_build_records.py", "tests/test_overall_test_full_sync.py", "tests/test_stage5.py",
    ]
    print("Isolated regression output:", output, flush=True)
    return int(pytest.main([*args, "-o", "cache_dir=" + str(output / "cache"),
                           "--junitxml=" + str(output / "results.xml")]))


if __name__ == "__main__":
    raise SystemExit(main())
