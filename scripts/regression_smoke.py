from __future__ import annotations

import argparse
from datetime import date

import httpx


def _ok(resp: httpx.Response, label: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"[FAIL] {label} -> {resp.status_code}: {resp.text}")
    print(f"[OK] {label} -> {resp.status_code}")


def run(base_url: str, username: str, password: str) -> None:
    with httpx.Client(base_url=base_url, timeout=20) as client:
        token_resp = client.post(
            "/auth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"username": username, "password": password},
        )
        _ok(token_resp, "POST /auth/token")
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("[FAIL] /auth/token 响应缺少 access_token")

        headers = {"Authorization": f"Bearer {access_token}"}

        me_resp = client.get("/auth/me", headers=headers)
        _ok(me_resp, "GET /auth/me")
        me_data = me_resp.json()
        role = me_data.get("role")
        print(f"[INFO] 当前用户: {me_data.get('username')} ({role})")

        versions_resp = client.get("/versions", headers=headers)
        _ok(versions_resp, "GET /versions")

        today = date.today().isoformat()
        summary_resp = client.get(
            f"/reports/summary?start_date={today}&end_date={today}",
            headers=headers,
        )
        _ok(summary_resp, "GET /reports/summary")

        version_bugs_resp = client.get("/reports/version-bugs", headers=headers)
        _ok(version_bugs_resp, "GET /reports/version-bugs")

        advanced_resp = client.get(
            f"/reports/advanced?start_date={today}&end_date={today}",
            headers=headers,
        )
        _ok(advanced_resp, "GET /reports/advanced")

        workbench_resp = client.get("/requirements/my-workbench?mode=all_pending", headers=headers)
        _ok(workbench_resp, "GET /requirements/my-workbench?mode=all_pending")

        if role == "admin":
            users_resp = client.get("/users", headers=headers)
            _ok(users_resp, "GET /users")
            data_overview_resp = client.get("/admin/data-overview", headers=headers)
            _ok(data_overview_resp, "GET /admin/data-overview")

    print("\n回归冒烟检查完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OmniQA 回归冒烟脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="服务地址，例如 http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin", help="登录用户名")
    parser.add_argument("--password", default="admin", help="登录密码")
    args = parser.parse_args()

    run(args.base_url.rstrip("/"), args.username, args.password)
