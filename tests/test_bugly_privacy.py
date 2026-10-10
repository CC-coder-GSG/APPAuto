from __future__ import annotations

import copy
import json

import pytest

from app.services.bugly_privacy import hide_user_ids


@pytest.mark.parametrize("key", ["用户ID", "用户 ID", "用户_id", "用户ＩＤ", "userId", "USER_ID", "User ID", "user-id", "UID"])
def test_user_id_fields_are_omitted_recursively(key):
    data = {"listFields": {key: "account-secret", "版本": "1.2.3"},
            "detailFields": {key: "account-secret", "设备ID": "device-useful"},
            "captureDiagnostics": {"nested": [{key: "account-secret", "frameCount": 2}]}}
    original = copy.deepcopy(data)
    result = hide_user_ids(data)
    assert result == {"listFields": {"版本": "1.2.3"}, "detailFields": {"设备ID": "device-useful"},
                      "captureDiagnostics": {"nested": [{"frameCount": 2}]}}
    assert data == original


@pytest.mark.parametrize("text", [
    "用户ID： account-secret\n设备：Pixel 9\n",
    "用户 ID = account-secret\r\n设备：Pixel 9\r\n",
    "用户ＩＤ\naccount-secret\n设备：Pixel 9\n",
    "用户ID\n\naccount-secret\n设备：Pixel 9\n",
    "userId\taccount-secret\n设备：Pixel 9\n",
    '用户ID: "account-secret"\n设备：Pixel 9\n',
    '设备：Pixel 9 | user_id="account-secret" | frameCount=2',
    '设备：Pixel 9, "User ID": "account-secret", "frameCount": 2',
    '设备：Pixel 9 | UserId: account-secret | frameCount=2',
    '设备：Pixel 9 | UID: account-secret | frameCount=2',
])
def test_user_id_in_raw_text_is_hidden_without_losing_other_fields(text):
    result = hide_user_ids(text)
    assert "account-secret" not in result
    assert "Pixel 9" in result
    assert "用户" not in result
    assert "user" not in result.lower()


def test_user_id_with_spaces_and_empty_field():
    result = hide_user_ids("用户ID: account secret\n用户ID:\n设备：Pixel 9\n")
    assert result == "设备：Pixel 9\n"
    assert hide_user_ids("用户ID\n设备机型\nPixel 9") == "设备机型\nPixel 9"


def test_short_user_ids_do_not_damage_versions_and_stack_frame_numbers():
    result = hide_user_ids({"用户ID": "1", "rawDetailText": "用户ID: 1\n版本:1.2.3\n",
                            "stack": {"text": " at Main.java:1\n at Native.cpp:101"}, "frameCount": 1})
    assert result == {"rawDetailText": "版本:1.2.3\n",
                      "stack": {"text": " at Main.java:1\n at Native.cpp:101"}, "frameCount": 1}


def test_known_values_in_stack_and_diagnostics_are_masked():
    result = hide_user_ids({"detailFields": {"userId": "secret-account"},
                            "stack": {"text": "Exception for secret-account\n at Main.java:23"},
                            "captureDiagnostics": {"log": "Fetched secret-account", "domWaitTimedOut": False}})
    assert result["stack"]["text"] == "Exception for [已脱敏]\n at Main.java:23"
    assert result["captureDiagnostics"] == {"log": "Fetched [已脱敏]", "domWaitTimedOut": False}


def test_field_records_in_diagnostic_arrays_are_omitted():
    data = {"fields": [{"label": "用户ID", "value": "secret-account"}, {"key": "USER_ID", "text": "secret-account"},
                       {"label": "设备", "value": "Pixel"}], "log": "Fetched secret-account"}
    assert hide_user_ids(data) == {"fields": [{"label": "设备", "value": "Pixel"}], "log": "Fetched [已脱敏]"}


def test_report_identity_is_preserved_when_it_equals_a_user_id():
    assert hide_user_ids({"userId": "1001", "reportId": "1001", "issueId": "1001", "count": 1001}) == {
        "reportId": "1001", "issueId": "1001", "count": 1001,
    }


def test_escaped_user_id_fields_in_json_text_are_hidden():
    raw = json.dumps({"用户ID": "secret-account", "capture": {"userId": "secret-account", "frameCount": 2},
                      "log": "Fetched secret-account"})
    assert json.loads(hide_user_ids(raw)) == {"capture": {"frameCount": 2}, "log": "Fetched [已脱敏]"}
    assert hide_user_ids('{"frameCount": 2}') == '{"frameCount": 2}'
