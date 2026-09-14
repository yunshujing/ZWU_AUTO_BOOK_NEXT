# -*- coding: utf-8 -*-
"""
Config layering test script.

Verifies load_accounts() priority branches, _load_accounts_split() merge logic,
the two-phase scheduler (login-then-book), DRY RUN safety, and main-loop enabled skip.
Does NOT trigger real booking (only open_session is mocked).

Run: python test_config_layering.py
"""
import json
import os
import re
import sys
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')


def _reload_demo():
    if 'demo' in sys.modules:
        del sys.modules['demo']
    import demo
    return demo


def _run_main_loop_ex(demo, fail_on=None, book_fail_on=None, notify=None, notify_fail=None):
    """Drive the REAL production scheduler (demo.run_all), replacing only open_session.

    Nothing here touches the network, launches a browser, or books a seat: the fake
    session stands in for the logged-in state. Keep delegating to the real scheduler —
    a copied loop would keep passing after demo.py changes, which is the regression we
    need to catch.

    Returns dict: {'processed', 'booked', 'results', 'output'}
      booked 只包含真正进入抢座的账号 —— 用它可以直接断言"没有抢座"。
    """
    seen = []      # open_session 收到的 (username, password, room_id)
    booked = {}    # username -> 实际传给抢座的参数

    class FakeSession:
        def __init__(self, username):
            self.username = username

        def book(self, dday, start_hour, duration, **kwargs):
            booked[self.username] = {'dday': dday, 'begin': start_hour,
                                     'duration': duration, **kwargs}
            if book_fail_on and self.username == book_fail_on:
                return 'fail', '无可用座位', None
            return 'ok', 'mock success', 12920

    def fake_open_session(username, password, room_id, **kwargs):
        if fail_on and username == fail_on:
            raise RuntimeError("chrome failed to start")  # 模拟浏览器启动失败
        seen.append((username, password, room_id))
        return FakeSession(username), None

    demo.open_session = fake_open_session
    demo.notify = notify or (lambda *a, **k: None)
    demo.notify_fail = notify_fail or (lambda *a, **k: None)

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        results = demo.run_all(accounts, defaults, concurrency=1)
        demo.print_summary(results, demo._is_dry_run())
    output = f.getvalue()

    processed = []
    for username, password, room_id in seen:
        call = booked.get(username, {})
        processed.append({
            'username': username,
            'password': password,
            'room_id': room_id,
            'begin': call.get('begin'),
            'seat_ids': call.get('seat_ids'),
        })
    return {'processed': processed, 'booked': booked, 'results': results, 'output': output}


def _run_main_loop(demo):
    """Backward-compatible wrapper returning (processed, output)."""
    r = _run_main_loop_ex(demo)
    return r['processed'], r['output']


_ACCOUNTS_BACKUP = ACCOUNTS_FILE + '.testhidden'
_local_file_hidden = False


def _ensure_no_local_file():
    """
    真实本地配置会盖住测试要验证的环境变量分支，所以运行前把它临时挪开，
    测试结束（含异常/中断）后自动放回。

    刻意不采用「检测到就报错退出」的做法：那样开发者一旦配好账号文件，
    这套测试就再也跑不了了 —— 而配好账号文件才是正常状态。
    """
    global _local_file_hidden
    if os.path.exists(ACCOUNTS_FILE):
        os.replace(ACCOUNTS_FILE, _ACCOUNTS_BACKUP)
        _local_file_hidden = True
        print("提示: 已临时移开本地 %s，测试结束后自动恢复"
              % os.path.basename(ACCOUNTS_FILE))


def _restore_local_file():
    global _local_file_hidden
    if _local_file_hidden and os.path.exists(_ACCOUNTS_BACKUP):
        os.replace(_ACCOUNTS_BACKUP, ACCOUNTS_FILE)
        _local_file_hidden = False
        print("提示: 本地 %s 已恢复" % os.path.basename(ACCOUNTS_FILE))


def _clean_env():
    for k in ('ACCOUNTS', 'ACCOUNTS_CONFIG', 'PASSWORDS',
              'FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID',
              'FEISHU_PASSWORD_TABLE_ID', 'DRY_RUN'):
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
    _ensure_no_local_file()  # 真实配置已临时挪开，不会被覆盖
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
    r = _run_main_loop_ex(demo)

    assert len(r['results']) == 1, "expected 1 account, got %d" % len(r['results'])
    params = r['results'][0]['params']
    assert 'enabled' not in params, \
        "enabled must not leak into params, got: %s" % sorted(params.keys())
    assert params.get('room_id') == 3
    assert r['processed'][0]['room_id'] == 3, "resolved room_id must reach the session"
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


def test_18_account_exception_isolation():
    print("\n" + "=" * 60)
    print("Test 18: single account exception does not abort the batch")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "boom_user"},
        {"username": "good_user"},
        {"username": "after_user"},
    ])
    os.environ['PASSWORDS'] = json.dumps(
        {"boom_user": "p1", "good_user": "p2", "after_user": "p3"})
    demo = _reload_demo()

    notified = []
    failures = []
    r = _run_main_loop_ex(
        demo, fail_on='boom_user',
        notify=lambda user, *a, **k: notified.append(user),
        notify_fail=lambda user, reason, *a, **k: failures.append((user, reason)))

    # 核心断言：出错账号之后的账号仍然被登录（旧实现在这里会整批中断）
    assert [p['username'] for p in r['processed']] == ['good_user', 'after_user'], \
        "accounts after the failing one must still be attempted, got %s" % r['processed']

    by_name = {x['username']: x for x in r['results']}
    assert by_name['boom_user']['stat'] == 'fail', "failing account should be marked fail"
    assert by_name['good_user']['stat'] == 'ok'
    assert by_name['after_user']['stat'] == 'ok'

    # 出错账号必须收到失败通知，不能静默消失
    assert len(failures) == 1, \
        "failing account should still be notified, got %s" % failures
    assert failures[0][0] == 'boom_user'
    assert notified == ['good_user', 'after_user'], \
        "successful accounts must still be notified, got %s" % notified

    # 通知里应该是人话，而不是异常原文
    assert 'chrome' not in failures[0][1].lower(), \
        "raw exception text must not leak into the notice, got %s" % failures[0][1]
    print("[PASS] exception isolated to its own account; every account still notified")


def test_19_summary_reports_every_account():
    print("\n" + "=" * 60)
    print("Test 19: summary accounts for every account (no silent disappearance)")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "ok_user"},
        {"username": "fail_user"},
        {"username": "off_user", "enabled": False},
    ])
    os.environ['PASSWORDS'] = json.dumps(
        {"ok_user": "p1", "fail_user": "p2", "off_user": "p3"})
    demo = _reload_demo()
    output = _run_main_loop_ex(demo, book_fail_on='fail_user')['output']

    assert "1 成功 / 1 失败 / 1 跳过" in output, "summary counts wrong:\n%s" % output
    assert 'fail_user' in output and '无可用座位' in output, "failure reason must be listed"
    assert 'off_user' in output and '已停用' in output, "skip reason must be listed"
    print("[PASS] summary lists counts plus every failure/skip reason")


def test_20_dry_run_sends_no_notification():
    print("\n" + "=" * 60)
    print("Test 20: DRY RUN verifies login only, never sends a notification")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['DRY_RUN'] = '1'
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([{"username": "dry_user"}])
    os.environ['PASSWORDS'] = json.dumps({"dry_user": "p1"})
    demo = _reload_demo()

    assert demo._is_dry_run() is True, "DRY_RUN=1 must be recognised"
    for falsy in ('0', 'false', 'no', ''):
        os.environ['DRY_RUN'] = falsy
        assert demo._is_dry_run() is False, "DRY_RUN=%r must not enable dry run" % falsy
    os.environ['DRY_RUN'] = '1'

    notices = []
    failures = []
    collect = dict(notify=lambda *a, **k: notices.append(a),
                   notify_fail=lambda *a, **k: failures.append(a))
    r = _run_main_loop_ex(demo, **collect)

    # 核心：DRY RUN 绝不进入抢座阶段（防误约的硬保险）
    assert r['booked'] == {}, \
        "DRY RUN MUST NOT reach the booking call, got %s" % r['booked']
    assert r['processed'], "login must still happen (that is the whole point)"
    assert notices == [], \
        "DRY RUN must never send a success notice (would be a fake booking), got %s" % notices
    assert failures == [], "DRY RUN must not send failure notices either"
    assert 'DRY RUN' in r['output'] and '未发起任何预约请求' in r['output']
    assert '[dry-run] dry_user 登录链路正常' in r['output']

    # 登录失败时同样不发通知，但要如实报告
    r2 = _run_main_loop_ex(demo, fail_on='dry_user', **collect)
    assert r2['results'][0]['stat'] == 'fail'
    assert failures == [], "DRY RUN must not send failure notices either"
    assert '登录链路失败' in r2['output'], "dry run must report the failure honestly"
    print("[PASS] dry run never books, sends no notice, reports honestly")


def test_21_appoint_dry_run_skips_booking():
    print("\n" + "=" * 60)
    print("Test 21: appoint_zwulib(dry_run=True) never reaches the booking call")
    print("=" * 60)
    import zwulib

    calls = {'login': 0, 'uid': 0, 'book': 0, 'quit': 0}

    class FakeSession:
        def book(self, *args, **kwargs):
            calls['book'] += 1
            return 'ok', 'should never happen in dry run', 12920

    class FakeBooker:
        """Stands in for SeatAutoBooker so no browser is launched."""

        def __init__(self, username, password, room_id, seat_ids):
            self.driver = self

        def login(self):
            calls['login'] += 1
            return 0

        def get_user_info(self):
            calls['uid'] += 1
            return 0

        def to_session(self):
            return FakeSession()

        def quit(self):
            calls['quit'] += 1

    real = zwulib.SeatAutoBooker
    zwulib.SeatAutoBooker = FakeBooker
    try:
        f = io.StringIO()
        with redirect_stdout(f):
            stat, msg, seatid = zwulib.appoint_zwulib('u', 'p', dry_run=True)
    finally:
        zwulib.SeatAutoBooker = real

    assert stat == 'ok', "successful login under dry run should report ok, got %s" % stat
    assert calls['login'] == 1, "login must still run (that is the whole point)"
    assert calls['uid'] == 1, "uid lookup must still run to prove the session works"
    assert calls['book'] == 0, \
        "DRY RUN MUST NOT reach the booking call, got %d call(s)" % calls['book']
    assert calls['quit'] == 1, "the browser must still be cleaned up"
    assert 'DRY RUN' in f.getvalue()
    print("[PASS] dry run stops before booking and still cleans up the browser")


def test_22_two_phase_login_before_booking():
    print("\n" + "=" * 60)
    print("Test 22: every login finishes before the first booking; concurrency capped")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps(
        [{"username": "u%d" % i} for i in range(1, 7)])
    os.environ['PASSWORDS'] = json.dumps({"u%d" % i: "p" for i in range(1, 7)})
    demo = _reload_demo()

    events = []
    live = {'n': 0, 'max': 0}
    lock = threading.Lock()

    class FakeSession:
        def __init__(self, username):
            self.username = username

        def book(self, *args, **kwargs):
            with lock:
                live['n'] += 1
                live['max'] = max(live['max'], live['n'])
            try:
                events.append(('book', self.username))
                time.sleep(0.05)  # 模拟网络往返，给并发留出重叠窗口
                return 'ok', 'mock success', 12920
            finally:
                with lock:
                    live['n'] -= 1

    def fake_open_session(username, password, room_id, **kwargs):
        events.append(('login', username))
        time.sleep(0.02)
        return FakeSession(username), None

    demo.open_session = fake_open_session
    demo.notify = demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        # 关掉抖动：抖动的作用就是把请求错开，会与"必须重叠"的断言互相冲突。
        # 抖动本身的行为由 Test 24 单独覆盖。
        defaults['concurrency-jitter'] = 0
        demo.run_all(accounts, defaults, concurrency=3)
    output = f.getvalue()

    logins = [e for e in events if e[0] == 'login']
    books = [e for e in events if e[0] == 'book']
    assert len(logins) == 6, "expected 6 logins, got %d" % len(logins)
    assert len(books) == 6, "expected 6 bookings, got %d" % len(books)

    # 核心保证：所有登录都排在第一个抢座之前 —— 登录不再占用抢座窗口
    first_book = min(i for i, e in enumerate(events) if e[0] == 'book')
    late_logins = [e for e in logins if events.index(e) > first_book]
    assert not late_logins, \
        "every login must finish before the first booking; late=%s order=%s" % (
            late_logins, events)

    # 并发上限生效，且确实发生了并发（而不是退化成串行）
    assert live['max'] <= 3, "concurrency cap exceeded: %d" % live['max']
    assert live['max'] >= 2, \
        "expected overlapping bookings with concurrency=3, max was %d" % live['max']
    assert '并发 3' in output
    print("[PASS] all logins precede bookings; concurrency respected (max %d)" % live['max'])


def test_23_concurrent_sessions_never_share_cookies():
    print("\n" + "=" * 60)
    print("Test 23: concurrent sessions keep their own Cookie (no cross-talk)")
    print("=" * 60)
    import zwulib

    N = 5
    lock = threading.Lock()
    seen = []
    start_gate = threading.Barrier(N)  # 让 N 个线程尽量同时开抢

    class FakeResp:
        def __init__(self, payload):
            self._p = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._p

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        m = re.search(r'seatBookers\[0\]=(\d+)', data or '')
        with lock:
            seen.append({'cookie': (headers or {}).get('Cookie'),
                         'uid': m.group(1) if m else None})
        return FakeResp({'CODE': 'ok', 'MESSAGE': 'mock'})

    def run(sess):
        start_gate.wait(timeout=10)
        return sess.book(2, 9, 12, seat_ids=[13263], max_retry=1)

    real_post = zwulib.requests.post
    zwulib.requests.post = fake_post
    try:
        sessions = [zwulib.SeatSession('u%d' % i, 'cookie-u%d' % i, 1000 + i, 2)
                    for i in range(1, N + 1)]
        with ThreadPoolExecutor(max_workers=N) as pool:
            results = list(pool.map(run, sessions))
    finally:
        zwulib.requests.post = real_post

    assert len(seen) == N, "expected %d booking requests, got %d" % (N, len(seen))
    assert all(r[0] == 'ok' for r in results), \
        "all mock bookings should succeed, got %s" % results

    # 核心断言：每个 Cookie 只能配对自己那个 uid，绝不能串号
    pairs = {s['cookie']: s['uid'] for s in seen}
    assert len(pairs) == N, \
        "each session must send a distinct cookie, got %s" % sorted(pairs)
    for i in range(1, N + 1):
        got = pairs.get('cookie-u%d' % i)
        assert got == str(1000 + i), \
            "cookie-u%d must carry uid %d, got %s (all=%s)" % (i, 1000 + i, got, pairs)

    # 每个会话必须持有各自独立的 headers 对象（共用会导致 Cookie 被互相覆盖）
    assert len({id(s.headers) for s in sessions}) == N, \
        "each session must own a separate headers dict"
    uids = [s.user_data['uid'] for s in sessions]
    assert uids == [1001, 1002, 1003, 1004, 1005], "session uids must not mix up: %s" % uids
    print("[PASS] %d concurrent sessions: cookies/uids stayed paired, headers isolated" % N)


def test_24_jitter_applies_only_when_concurrent():
    print("\n" + "=" * 60)
    print("Test 24: request jitter applies only when concurrency > 1 and jitter > 0")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps(
        [{"username": "u%d" % i} for i in range(1, 5)])
    os.environ['PASSWORDS'] = json.dumps({"u%d" % i: "p" for i in range(1, 5)})

    def harness(concurrency, jitter):
        demo = _reload_demo()

        class FakeSession:
            def __init__(self, username):
                self.username = username

            def book(self, *a, **k):
                return 'ok', 'mock success', 12920

        demo.open_session = lambda u, p, r, **kw: (FakeSession(u), None)
        demo.notify = demo.notify_fail = lambda *a, **k: None

        f = io.StringIO()
        with redirect_stdout(f):
            accounts = demo.load_accounts()
            defaults = demo.load_booking_config()
            defaults['concurrency-jitter'] = jitter
            demo.run_all(accounts, defaults, concurrency=concurrency)
        return f.getvalue()

    # 串行：绝不抖动（避免白白增加耗时）
    out_serial = harness(1, 0.8)
    assert '[隔离]' not in out_serial, "serial mode must not stagger requests"
    assert '随机错开' not in out_serial, "serial mode must not announce jitter"

    # 并发 + 抖动 > 0：应出现错开日志
    out_conc = harness(3, 0.5)
    assert '[隔离]' in out_conc, "concurrent mode with jitter must stagger requests"
    assert '随机错开 0~0.5s' in out_conc, "jitter amount should be reported: %s" % out_conc[-400:]

    # 并发 + 抖动 = 0：显式关闭后不应错开
    out_zero = harness(3, 0)
    assert '[隔离]' not in out_zero, "jitter=0 must disable staggering"
    assert '随机错开' not in out_zero
    print("[PASS] jitter only when concurrent; serial and jitter=0 stay untouched")


def test_25_login_retry_rebuilds_browser():
    print("\n" + "=" * 60)
    print("Test 25: login failure retries on a fresh browser; success aborts retries")
    print("=" * 60)
    import zwulib

    # --- 场景 A：前两次登录失败，第三次成功 ---
    log = {'built': 0, 'quit': 0, 'login': []}

    class FlakyBooker:
        def __init__(self, username, password, room_id, seat_ids):
            self.driver = self
            self.uid = 12345
            log['built'] += 1
            self.n = log['built']

        def login(self):
            log['login'].append(self.n)
            return 0 if self.n >= 3 else -1   # 前两次失败

        def get_user_info(self):
            return 0

        def to_session(self):
            return zwulib.SeatSession('u', 'cookie', 1, 2)

        def quit(self):
            log['quit'] += 1

    real = zwulib.SeatAutoBooker
    zwulib.SeatAutoBooker = FlakyBooker
    try:
        f = io.StringIO()
        with redirect_stdout(f):
            session, err = zwulib.open_session('u', 'p', 2, login_retry=2,
                                                login_retry_wait=0)
    finally:
        zwulib.SeatAutoBooker = real

    assert session is not None, "第三次尝试应成功，实际 err=%s" % err
    assert err is None
    assert log['built'] == 3, \
        "每次重试都应重建浏览器实例（共 3 次），实际 %d 次" % log['built']
    assert log['quit'] == 3, \
        "每个失败的实例都必须被关闭，实际 quit %d 次" % log['quit']
    assert '登录重试' in f.getvalue(), "重试时应打印提示"
    assert '尝试次数' in f.getvalue(), "多次尝试时 timer 应带尝试次数"

    # --- 场景 B：第一次就成功，不应重试 ---
    log2 = {'built': 0}

    class OkBooker(FlakyBooker):
        def __init__(self, username, password, room_id, seat_ids):
            super().__init__(username, password, room_id, seat_ids)
            log2['built'] += 1

        def login(self):
            return 0

    zwulib.SeatAutoBooker = OkBooker
    try:
        with redirect_stdout(io.StringIO()):
            session2, err2 = zwulib.open_session('u', 'p', 2, login_retry=2,
                                                 login_retry_wait=0)
    finally:
        zwulib.SeatAutoBooker = real

    assert session2 is not None and err2 is None
    assert log2['built'] == 1, \
        "登录成功就不该重试，实际构造了 %d 次" % log2['built']

    # --- 场景 C：全部失败时返回最后一次的原因，且实例都被关闭 ---
    log3 = {'built': 0, 'quit': 0}

    class DeadBooker(FlakyBooker):
        def __init__(self, username, password, room_id, seat_ids):
            super().__init__(username, password, room_id, seat_ids)
            log3['built'] += 1

        def login(self):
            return -1

        def quit(self):
            log3['quit'] += 1

    zwulib.SeatAutoBooker = DeadBooker
    try:
        with redirect_stdout(io.StringIO()):
            session3, err3 = zwulib.open_session('u', 'p', 2, login_retry=2,
                                                 login_retry_wait=0)
    finally:
        zwulib.SeatAutoBooker = real

    assert session3 is None
    assert err3 == '登录失败', "全部失败应返回最后一次原因，实际 %s" % err3
    assert log3['built'] == 3 and log3['quit'] == 3, \
        "全部失败时也要清理干净，built=%d quit=%d" % (log3['built'], log3['quit'])

    # --- 场景 D：构造异常（浏览器起不来）不重试，直接向上抛 ---
    log4 = {'built': 0}

    class BrokenBooker:
        def __init__(self, username, password, room_id, seat_ids):
            log4['built'] += 1
            raise RuntimeError("chrome failed to start")

    zwulib.SeatAutoBooker = BrokenBooker
    raised = False
    try:
        with redirect_stdout(io.StringIO()):
            zwulib.open_session('u', 'p', 2, login_retry=2, login_retry_wait=0)
    except RuntimeError:
        raised = True
    finally:
        zwulib.SeatAutoBooker = real

    assert raised, "构造异常应向上抛出，由调用方处理"
    assert log4['built'] == 1, \
        "环境类异常不重试（重试只会更慢），实际尝试 %d 次" % log4['built']
    print("[PASS] retries rebuild browser, close every instance, skip retry on env errors")


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
        test_18_account_exception_isolation,
        test_19_summary_reports_every_account,
        test_20_dry_run_sends_no_notification,
        test_21_appoint_dry_run_skips_booking,
        test_22_two_phase_login_before_booking,
        test_23_concurrent_sessions_never_share_cookies,
        test_24_jitter_applies_only_when_concurrent,
        test_25_login_retry_rebuilds_browser,
    ]
    passed, failed = 0, 0
    try:
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
    finally:
        # 无论正常结束、断言失败还是被 Ctrl+C 打断，都要把真实配置放回去
        _clean_env()
        _restore_local_file()
    print("\n" + "=" * 60)
    print("Result: %d passed, %d failed (total %d)" % (passed, failed, len(tests)))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
