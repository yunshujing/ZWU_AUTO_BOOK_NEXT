# -*- coding: utf-8 -*-
"""
Config layering test script.

Verifies load_accounts() priority branches, _load_accounts_split() merge logic,
and main-loop enabled skip. Does NOT trigger real booking (mocks appoint_zwulib).

Run: python test_config_layering.py
"""
import json
import os
import sys
import io
from contextlib import redirect_stdout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')


def _reload_demo():
    if 'demo' in sys.modules:
        del sys.modules['demo']
    import demo
    return demo


def _run_main_loop(demo):
    """Run the main loop with mocked appoint/notify, return processed accounts + output."""
    processed = []

    def fake_appoint(username, password, **kwargs):
        processed.append({
            'username': username,
            'password': password,
            'room_id': kwargs.get('room_id'),
            'begin': kwargs.get('begin'),
            'seat_ids': kwargs.get('seat_ids'),
        })
        return 'ok', 'mock success', 12920

    demo.appoint_zwulib = fake_appoint
    demo.notify = lambda *a, **k: None
    demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        for i, account in enumerate(accounts, 1):
            username = account.get('username', '')
            password = account.get('password', '')
            if not username or not password:
                continue
            if account.get('enabled', True) is False:
                continue
            params = {**defaults, **{k: v for k, v in account.items()
                      if k not in ('username', 'password', 'enabled')}}
            demo.appoint_zwulib(
                username, password,
                room_id=params.get('room_id'),
                dday=params.get('dday'),
                begin=params.get('begin'),
                duration=params.get('duration'),
                seat_ids=params.get('seat_ids'),
                cron_delta_minutes=params.get('cron-delta-minutes', 5),
                max_retry=params.get('max-retry', 20),
            )
    return processed, f.getvalue()


def _ensure_no_local_file():
    if os.path.exists(ACCOUNTS_FILE):
        raise RuntimeError("Local file %s exists, will interfere with branch tests" % ACCOUNTS_FILE)


def _clean_env():
    for k in ('ACCOUNTS', 'ACCOUNTS_CONFIG', 'PASSWORDS',
              'FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID',
              'FEISHU_PASSWORD_TABLE_ID'):
        os.environ.pop(k, None)


def test_1_old_accounts_compat():
    print("\n" + "=" * 60)
    print("Test 1: backward compat - ACCOUNTS old usage")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS'] = json.dumps([
        {"username": "old_user1", "password": "old_pass1"},
        {"username": "old_user2", "password": "old_pass2", "room_id": 4, "begin": 21},
    ])
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts, got %d" % len(processed)
    assert processed[0]['username'] == 'old_user1'
    assert processed[0]['password'] == 'old_pass1'
    assert processed[1]['username'] == 'old_user2'
    assert processed[1]['password'] == 'old_pass2'
    assert processed[1]['room_id'] == 4
    assert processed[1]['begin'] == 21
    assert "old usage" in output or "lao yong fa" in output.lower() or "ACCOUNTS" in output
    print("[PASS] old usage: 2 accounts processed, password from ACCOUNTS, override fields work")


def test_2_new_split_normal():
    print("\n" + "=" * 60)
    print("Test 2: new split path - ACCOUNTS_CONFIG + PASSWORDS")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "new_user1"},
        {"username": "new_user2", "room_id": 4, "begin": 21, "duration": 9, "seat_ids": [12920, 12921]},
    ])
    os.environ['PASSWORDS'] = json.dumps({"new_user1": "pass1", "new_user2": "pass2"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts, got %d" % len(processed)
    assert processed[0]['username'] == 'new_user1'
    assert processed[0]['password'] == 'pass1'
    assert processed[1]['username'] == 'new_user2'
    assert processed[1]['password'] == 'pass2'
    assert processed[1]['room_id'] == 4
    assert processed[1]['begin'] == 21
    assert processed[1]['seat_ids'] == [12920, 12921]
    print("[PASS] new path: 2 accounts loaded, password matched from PASSWORDS, overrides work")


def test_3_enabled_skip():
    print("\n" + "=" * 60)
    print("Test 3: enabled disable switch")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "active_user"},
        {"username": "disabled_user", "enabled": False, "room_id": 2},
        {"username": "active_user2", "enabled": True},
    ])
    os.environ['PASSWORDS'] = json.dumps({"active_user": "p1", "disabled_user": "p2", "active_user2": "p3"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts (1 disabled), got %d" % len(processed)
    usernames = [a['username'] for a in processed]
    assert 'disabled_user' not in usernames
    assert 'active_user' in usernames
    assert 'active_user2' in usernames
    print("[PASS] enabled=false account skipped with log, enabled=true and default work")


def test_4_password_missing():
    print("\n" + "=" * 60)
    print("Test 4: partial password missing in PASSWORDS")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "has_pwd"},
        {"username": "no_pwd"},
    ])
    os.environ['PASSWORDS'] = json.dumps({"has_pwd": "secret"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 1, "expected 1 account (1 missing pwd), got %d" % len(processed)
    assert processed[0]['username'] == 'has_pwd'
    assert "PASSWORDS" in output
    print("[PASS] account with missing password skipped with warning, others proceed")


def test_5_passwords_absent_fail_loud():
    print("\n" + "=" * 60)
    print("Test 5: fail loud - ACCOUNTS_CONFIG set but PASSWORDS absent")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([{"username": "user1"}])
    demo = _reload_demo()
    accounts = demo.load_accounts()
    assert accounts == [], "PASSWORDS absent should return empty list"
    print("[PASS] PASSWORDS absent -> fail loud returns empty list")


def test_6_invalid_json():
    print("\n" + "=" * 60)
    print("Test 6: invalid JSON handling")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = "{not valid json"
    demo = _reload_demo()
    accounts = demo.load_accounts()
    assert accounts == [], "invalid JSON should return empty list"
    print("[PASS] invalid JSON reports error and returns empty list, no crash")


def test_7_local_file_priority():
    print("\n" + "=" * 60)
    print("Test 7: local file has highest priority")
    print("=" * 60)
    tmp_content = [{"username": "local_user", "password": "local_pass"}]
    if os.path.exists(ACCOUNTS_FILE):
        raise RuntimeError("%s already exists, skip test to avoid overwriting real config" % ACCOUNTS_FILE)
    try:
        with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tmp_content, f)
        os.environ['ACCOUNTS'] = json.dumps([{"username": "env_user", "password": "env_pass"}])
        os.environ['ACCOUNTS_CONFIG'] = json.dumps([{"username": "var_user"}])
        os.environ['PASSWORDS'] = json.dumps({"var_user": "p"})
        demo = _reload_demo()
        accounts = demo.load_accounts()
        assert len(accounts) == 1, "should return only the local file account"
        assert accounts[0]['username'] == 'local_user'
        print("[PASS] local file takes priority, env vars ignored")
    finally:
        if os.path.exists(ACCOUNTS_FILE):
            os.remove(ACCOUNTS_FILE)


def test_8_enabled_field_not_in_params():
    print("\n" + "=" * 60)
    print("Test 8: enabled field does not leak into params")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "user1", "enabled": True, "room_id": 3},
    ])
    os.environ['PASSWORDS'] = json.dumps({"user1": "p1"})
    demo = _reload_demo()
    captured = {}

    def fake_appoint(username, password, **kwargs):
        captured.update(kwargs)
        return 'ok', 'mock', 12920

    demo.appoint_zwulib = fake_appoint
    demo.notify = lambda *a, **k: None
    demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        for i, account in enumerate(accounts, 1):
            username = account.get('username', '')
            password = account.get('password', '')
            if not username or not password:
                continue
            if account.get('enabled', True) is False:
                continue
            params = {**defaults, **{k: v for k, v in account.items()
                      if k not in ('username', 'password', 'enabled')}}
            demo.appoint_zwulib(
                username, password,
                room_id=params.get('room_id'),
                dday=params.get('dday'),
                begin=params.get('begin'),
                duration=params.get('duration'),
                seat_ids=params.get('seat_ids'),
                cron_delta_minutes=params.get('cron-delta-minutes', 5),
                max_retry=params.get('max-retry', 20),
            )

    assert 'enabled' not in captured, "enabled should not be in params, got: %s" % list(captured.keys())
    assert captured.get('room_id') == 3
    print("[PASS] enabled field properly excluded from params")


# ============================================
# Feishu (Lark Base) mock tests
# ============================================

class _FakeResponse:
    """Mock requests.Response for feishu tests."""
    def __init__(self, json_data):
        self._data = json_data
    def json(self):
        return self._data


def _mock_feishu_api(token_resp, records_resp_list, monkey_requests):
    """
    Set up mock for requests.post (token) and requests.get (records).
    records_resp_list: list of page responses (for pagination testing)
    """
    call_idx = {'i': 0}

    def fake_post(url, **kwargs):
        return _FakeResponse(token_resp)

    def fake_get(url, **kwargs):
        idx = call_idx['i']
        call_idx['i'] += 1
        if idx < len(records_resp_list):
            return _FakeResponse(records_resp_list[idx])
        return _FakeResponse({'code': 0, 'data': {'items': [], 'has_more': False}})

    monkey_requests.post = fake_post
    monkey_requests.get = fake_get


def test_9_feishu_normal():
    print("\n" + "=" * 60)
    print("Test 9: feishu normal path - mock API + PASSWORDS merge")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"20210001": "pwd1", "20210002": "pwd2"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    records_resp = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'rec1', 'fields': {'username': '20210001', 'enabled': True, 'room_id': 2, 'begin': 12}},
                {'record_id': 'rec2', 'fields': {'username': '20210002', 'enabled': False, 'room_id': 4, 'begin': 21, 'seat_ids': '12920,12921'}},
            ],
            'has_more': False,
        }
    }

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [records_resp], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 2, "expected 2 accounts, got %d" % len(accounts)
    assert accounts[0]['username'] == '20210001'
    assert accounts[0]['password'] == 'pwd1'
    assert accounts[0]['room_id'] == 2
    assert accounts[0]['begin'] == 12
    assert accounts[0]['enabled'] is True
    assert accounts[1]['username'] == '20210002'
    assert accounts[1]['password'] == 'pwd2'
    assert accounts[1]['seat_ids'] == [12920, 12921]
    assert accounts[1]['enabled'] is False
    print("[PASS] feishu normal: 2 accounts loaded, fields parsed, passwords merged")


def test_10_feishu_pagination():
    print("\n" + "=" * 60)
    print("Test 10: feishu pagination - multiple pages")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"a": "pa", "b": "pb", "c": "pc"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    page1 = {
        'code': 0,
        'data': {
            'items': [{'record_id': 'r1', 'fields': {'username': 'a'}}],
            'has_more': True,
            'page_token': 'next',
        }
    }
    page2 = {
        'code': 0,
        'data': {
            'items': [{'record_id': 'r2', 'fields': {'username': 'b'}}, {'record_id': 'r3', 'fields': {'username': 'c'}}],
            'has_more': False,
        }
    }

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [page1, page2], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 3, "expected 3 accounts (pagination), got %d" % len(accounts)
    usernames = [a['username'] for a in accounts]
    assert usernames == ['a', 'b', 'c']
    print("[PASS] feishu pagination: 3 accounts across 2 pages")


def test_11_feishu_missing_secrets():
    print("\n" + "=" * 60)
    print("Test 11: feishu fail loud - missing FEISHU secrets")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    # Missing other 3 feishu secrets

    demo = _reload_demo()
    accounts = demo.load_accounts()
    assert accounts == [], "missing feishu secrets should return empty list"
    print("[PASS] feishu missing secrets -> fail loud returns empty")


def test_12_feishu_token_error():
    print("\n" + "=" * 60)
    print("Test 12: feishu fail loud - token API error")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"a": "pa"})

    token_resp = {'code': 99991663, 'msg': 'invalid app_id'}

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert accounts == [], "token error should return empty list"
    print("[PASS] feishu token error -> fail loud returns empty")


def test_13_feishu_records_error():
    print("\n" + "=" * 60)
    print("Test 13: feishu fail loud - records API error (permission)")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"a": "pa"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    records_resp = {'code': 1254030, 'msg': 'no permission'}

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [records_resp], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert accounts == [], "records API error should return empty list"
    print("[PASS] feishu records error -> fail loud returns empty")


def test_14_feishu_password_missing():
    print("\n" + "=" * 60)
    print("Test 14: feishu - partial password missing in PASSWORDS")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"has_pwd": "secret"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    records_resp = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'r1', 'fields': {'username': 'has_pwd'}},
                {'record_id': 'r2', 'fields': {'username': 'no_pwd'}},
            ],
            'has_more': False,
        }
    }

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [records_resp], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 1, "expected 1 account (1 missing pwd), got %d" % len(accounts)
    assert accounts[0]['username'] == 'has_pwd'
    print("[PASS] feishu partial password missing -> skip with warning")


def test_15_feishu_text_field_as_list():
    print("\n" + "=" * 60)
    print("Test 15: feishu - text field returns list format")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"20210001": "pwd1"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    # Feishu text fields sometimes return [{"text": "value"}] format
    records_resp = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'r1', 'fields': {
                    'username': [{'text': '20210001'}],
                    'seat_ids': [{'text': '12920,12921'}],
                }},
            ],
            'has_more': False,
        }
    }

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    _mock_feishu_api(token_resp, [records_resp], fake_requests)
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 1, "expected 1 account, got %d" % len(accounts)
    assert accounts[0]['username'] == '20210001'
    assert accounts[0]['seat_ids'] == [12920, 12921]
    print("[PASS] feishu list-format text fields parsed correctly")


def test_16_feishu_password_table_priority_and_fallback():
    print("\n" + "=" * 60)
    print("Test 16: feishu password table - table wins over PASSWORDS, secret as fallback")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'tbl_accounts'
    os.environ['FEISHU_PASSWORD_TABLE_ID'] = 'tbl_pwd'
    os.environ['PASSWORDS'] = json.dumps({"userA": "secret_from_github", "userB": "pwdB"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    account_records = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'r1', 'fields': {'username': 'userA'}},
                {'record_id': 'r2', 'fields': {'username': 'userB'}},
            ],
            'has_more': False,
        }
    }
    pwd_records = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'p1', 'fields': {'username': 'userA', 'password': 'pwdA_from_feishu'}},
            ],
            'has_more': False,
        }
    }

    def fake_post(url, **kwargs):
        return _FakeResponse(token_resp)

    def fake_get(url, **kwargs):
        # 按_URL 中的 table_id 区分账号表和密码表
        if 'tbl_pwd' in url:
            return _FakeResponse(pwd_records)
        return _FakeResponse(account_records)

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    fake_requests.post = fake_post
    fake_requests.get = fake_get
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 2, "expected 2 accounts, got %d" % len(accounts)
    by_user = {a['username']: a['password'] for a in accounts}
    assert by_user['userA'] == 'pwdA_from_feishu', "password table should win, got %s" % by_user['userA']
    assert by_user['userB'] == 'pwdB', "user missing from table should fall back to PASSWORDS"
    print("[PASS] password table takes priority, PASSWORDS covers users missing from table")


def test_17_feishu_password_table_list_format_without_passwords_secret():
    print("\n" + "=" * 60)
    print("Test 17: feishu password table - list-format fields, PASSWORDS secret absent")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'tbl_accounts'
    os.environ['FEISHU_PASSWORD_TABLE_ID'] = 'tbl_pwd'
    # 故意不设置 PASSWORDS：只要密码表覆盖全部账号就不需要 Secret

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    account_records = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'r1', 'fields': {'username': 'userC'}},
                {'record_id': 'r2', 'fields': {'username': 'userD'}},
            ],
            'has_more': False,
        }
    }
    # 密码表文本字段用 [{"text": ...}] 列表格式（飞书有时返回这种结构）
    pwd_records = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'p1', 'fields': {
                    'username': [{'text': 'userC'}],
                    'password': [{'text': 'pwdC'}],
                }},
            ],
            'has_more': False,
        }
    }

    def fake_post(url, **kwargs):
        return _FakeResponse(token_resp)

    def fake_get(url, **kwargs):
        if 'tbl_pwd' in url:
            return _FakeResponse(pwd_records)
        return _FakeResponse(account_records)

    demo = _reload_demo()
    import types
    fake_requests = types.SimpleNamespace()
    fake_requests.post = fake_post
    fake_requests.get = fake_get
    demo.requests = fake_requests

    import io
    from contextlib import redirect_stdout
    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
    assert len(accounts) == 1, "userD has no password anywhere, only userC expected, got %d" % len(accounts)
    assert accounts[0]['username'] == 'userC'
    assert accounts[0]['password'] == 'pwdC'
    assert "userD" in f.getvalue() and "未找到对应密码" in f.getvalue(), "userD skip should be logged"
    print("[PASS] list-format password fields parsed, missing user skipped loudly, no PASSWORDS needed")


if __name__ == '__main__':
    tests = [
        test_1_old_accounts_compat,
        test_2_new_split_normal,
        test_3_enabled_skip,
        test_4_password_missing,
        test_5_passwords_absent_fail_loud,
        test_6_invalid_json,
        test_7_local_file_priority,
        test_8_enabled_field_not_in_params,
        test_9_feishu_normal,
        test_10_feishu_pagination,
        test_11_feishu_missing_secrets,
        test_12_feishu_token_error,
        test_13_feishu_records_error,
        test_14_feishu_password_missing,
        test_15_feishu_text_field_as_list,
        test_16_feishu_password_table_priority_and_fallback,
        test_17_feishu_password_table_list_format_without_passwords_secret,
    ]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print("[FAIL] %s - %s" % (t.__name__, e))
            failed += 1
        except Exception as e:
            print("[FAIL] %s - %s: %s" % (t.__name__, type(e).__name__, e))
            failed += 1
    _clean_env()
    print("\n" + "=" * 60)
    print("Result: %d passed, %d failed (total %d)" % (passed, failed, len(tests)))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
