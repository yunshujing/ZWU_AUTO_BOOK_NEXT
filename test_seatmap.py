# -*- coding: utf-8 -*-
"""
Seat number (座位号) resolution tests.

Verifies zwu_lib.xlsx loading, seats→seat_ids conversion, invalid input
handling, demo.py priority logic (seat_ids > seats, account-level > defaults),
feishu seats column parsing, and main-loop end-to-end wiring.
Does NOT trigger real booking (mocks appoint_zwulib).

Run: python test_seatmap.py
"""
import io
import json
import os
import sys
import types
from contextlib import redirect_stdout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from seatmap import load_seat_map, resolve_seats, get_seat_info


def _reload_demo():
    if 'demo' in sys.modules:
        del sys.modules['demo']
    import demo
    return demo


def _clean_env():
    for k in ('ACCOUNTS', 'ACCOUNTS_CONFIG', 'PASSWORDS',
              'FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID',
              'FEISHU_PASSWORD_TABLE_ID', 'DRY_RUN'):
        os.environ.pop(k, None)


_ACCOUNTS_BACKUP = ACCOUNTS_FILE + '.testhidden'
_local_file_hidden = False


def _ensure_no_local_file():
    """
    真实本地配置会盖住测试要验证的环境变量分支，所以运行前把它临时挪开，
    测试结束后自动放回（而不是直接报错退出）。
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


# 已核对的已知映射（README 默认 seat_ids 12920/12921 实际是自习室114的113/114号座位）
KNOWN_114 = {113: 12920, 114: 12921, 200: 13009}
KNOWN_112 = {1: 13344, 298: 13688}
# README 自习室编号对照的各室座位数
EXPECTED_COUNTS = {0: 298, 1: 316, 2: 216, 3: 242, 4: 248, 5: 242, 6: 208, 7: 224, 8: 174}


def test_1_seat_map_structure():
    print("\n" + "=" * 60)
    print("Test 1: seat map structure - 9 rooms, counts match README")
    print("=" * 60)
    seat_map = load_seat_map()
    assert sorted(seat_map.keys()) == list(range(9)), "should cover room_id 0-8"
    for room_id, count in EXPECTED_COUNTS.items():
        actual = len(seat_map[room_id])
        assert actual == count, "room %d should have %d seats, got %d" % (room_id, count, actual)
    print("[PASS] seat map loaded once, all 9 rooms match documented seat counts")


def test_2_known_mapping():
    print("\n" + "=" * 60)
    print("Test 2: known seat number -> seat ID mappings")
    print("=" * 60)
    seat_map = load_seat_map()
    for no, sid in KNOWN_114.items():
        assert seat_map[2][no] == sid, "自习室114 座位号 %d should be %d, got %s" % (no, sid, seat_map[2][no])
    for no, sid in KNOWN_112.items():
        assert seat_map[0][no] == sid, "自习室112 座位号 %d should be %d, got %s" % (no, sid, seat_map[0][no])
    print("[PASS] verified mappings correct (wrong conversion would book the wrong seat)")


def test_3_unique_title_per_room():
    print("\n" + "=" * 60)
    print("Test 3: seat numbers unique within each room (dict lookup precondition)")
    print("=" * 60)
    seat_map = load_seat_map()
    total = 0
    for room_id, room_map in seat_map.items():
        assert len(room_map) == len(set(room_map.keys())), "room %d has duplicate seat numbers" % room_id
        total += len(room_map)
    assert total == 2168, "expected 2168 seats in total, got %d" % total
    print("[PASS] no duplicate seat numbers in any room, 2168 seats in total")


def test_4_resolve_valid():
    print("\n" + "=" * 60)
    print("Test 4: resolve valid seat numbers")
    print("=" * 60)
    f = io.StringIO()
    with redirect_stdout(f):
        result = resolve_seats(2, [113, 114])
    assert result == [12920, 12921], "got %s" % result
    assert "座位号 [113, 114] → 座位ID [12920, 12921]" not in f.getvalue()  # 转换打印在 demo 层，seatmap 只报警告
    print("[PASS] [113, 114] -> [12920, 12921]")


def test_5_resolve_mixed_invalid():
    print("\n" + "=" * 60)
    print("Test 5: mixed valid/invalid seat numbers - skip invalid, keep valid")
    print("=" * 60)
    # 自习室114 没有 93-102 号座位；'abc'/None 非法
    f = io.StringIO()
    with redirect_stdout(f):
        result = resolve_seats(2, [113, 95, 'abc', None])
    assert result == [12920], "got %s" % result
    output = f.getvalue()
    assert "无座位号 95" in output, "should warn about missing seat number 95"
    assert "不是数字" in output, "should warn about non-numeric input"
    print("[PASS] invalid entries warned and skipped, valid one preserved")


def test_6_resolve_all_invalid_returns_empty():
    print("\n" + "=" * 60)
    print("Test 6: all seat numbers invalid -> empty list (falls back to random)")
    print("=" * 60)
    f = io.StringIO()
    with redirect_stdout(f):
        result = resolve_seats(2, [93, 94, 102])
    assert result == [], "got %s" % result
    print("[PASS] empty result, caller falls back to random seat selection")


def test_7_resolve_invalid_room():
    print("\n" + "=" * 60)
    print("Test 7: invalid room_id -> empty list with warning")
    print("=" * 60)
    f = io.StringIO()
    with redirect_stdout(f):
        assert resolve_seats(9, [1]) == []
        assert resolve_seats(None, [1]) == []
    assert "无效的自习室编号" in f.getvalue()
    print("[PASS] invalid/missing room_id fails loud and returns empty")


def test_8_account_seats_beat_default_seat_ids():
    print("\n" + "=" * 60)
    print("Test 8: account-level seats win over default-layer seat_ids")
    print("=" * 60)
    demo = _reload_demo()
    account = {'username': 'u1', 'seats': [200], 'room_id': 2}
    defaults = {'seat_ids': [12920, 12921], 'seats': None, 'room_id': 2}
    f = io.StringIO()
    with redirect_stdout(f):
        result = demo.resolve_final_seats(account, defaults)
    assert result == [13009], "account seats [200] should convert to [13009], got %s" % result
    print("[PASS] default seat_ids did not shadow account-level seat numbers")


def test_9_same_level_ids_win_with_warning():
    print("\n" + "=" * 60)
    print("Test 9: seat_ids and seats both set on same level -> seat_ids wins + warn")
    print("=" * 60)
    demo = _reload_demo()
    account = {'username': 'u1', 'seat_ids': [42], 'seats': [113]}
    defaults = {'seat_ids': None, 'seats': None, 'room_id': 2}
    f = io.StringIO()
    with redirect_stdout(f):
        result = demo.resolve_final_seats(account, defaults)
    assert result == [42], "explicit seat_ids should win, got %s" % result
    assert "以 seat_ids 为准" in f.getvalue(), "should warn about ambiguous config"
    print("[PASS] seat_ids preferred and conflict surfaced")


def test_10_default_level_resolution():
    print("\n" + "=" * 60)
    print("Test 10: default-layer seats/seat_ids resolution")
    print("=" * 60)
    demo = _reload_demo()
    # 默认层只有 seats -> 转换
    f = io.StringIO()
    with redirect_stdout(f):
        result = demo.resolve_final_seats({'username': 'u1'},
                                          {'seat_ids': None, 'seats': [113, 114], 'room_id': 2})
    assert result == [12920, 12921], "got %s" % result
    # 默认层两层都有 -> seat_ids 胜出 + 警告
    f = io.StringIO()
    with redirect_stdout(f):
        result = demo.resolve_final_seats({'username': 'u1'},
                                          {'seat_ids': [7, 8], 'seats': [113], 'room_id': 2})
    assert result == [7, 8], "got %s" % result
    assert "以 seat_ids 为准" in f.getvalue()
    # 两层都没配 -> None（随机）
    result = demo.resolve_final_seats({'username': 'u1'}, {'seat_ids': None, 'seats': None, 'room_id': 2})
    assert result is None, "got %s" % result
    print("[PASS] default-layer seats convert, conflict warns, absent -> random (None)")


def _run_main_loop_with_mock(demo, captured):
    """Drive the REAL production scheduler (demo.run_all) with mocked login.

    Delegates to the real scheduler on purpose: a locally copied loop would keep
    passing after demo.py changes and mask regressions.
    """
    class FakeSession:
        def __init__(self, username):
            self.username = username

        def book(self, dday, start_hour, duration, **kwargs):
            captured.append({'username': self.username,
                             'seat_ids': kwargs.get('seat_ids')})
            return 'ok', 'mock success', 12920

    def fake_open_session(username, password, room_id, **kwargs):
        return FakeSession(username), None

    demo.open_session = fake_open_session
    demo.notify = lambda *a, **k: None
    demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        demo.run_all(accounts, defaults, concurrency=1)
    return f.getvalue()


def test_11_main_loop_seats_end_to_end():
    print("\n" + "=" * 60)
    print("Test 11: main loop end-to-end - ACCOUNTS_CONFIG seats -> converted seat_ids")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "seat_user", "room_id": 2, "seats": [113, 114]},
        {"username": "id_user", "room_id": 2, "seat_ids": [13009]},
    ])
    os.environ['PASSWORDS'] = json.dumps({"seat_user": "p1", "id_user": "p2"})
    demo = _reload_demo()
    captured = []
    output = _run_main_loop_with_mock(demo, captured)
    assert len(captured) == 2, "expected 2 accounts, got %d" % len(captured)
    assert captured[0]['seat_ids'] == [12920, 12921], "seats should convert, got %s" % captured[0]['seat_ids']
    assert captured[1]['seat_ids'] == [13009], "explicit seat_ids should pass through"
    assert "座位号 [113, 114] → 座位ID [12920, 12921]" in output
    print("[PASS] seats converted before booking, seat_ids passthrough unchanged")


def test_12_feishu_seats_field():
    print("\n" + "=" * 60)
    print("Test 12: feishu seats column parsing ('113,114' -> [113, 114])")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['FEISHU_APP_ID'] = 'fake_app_id'
    os.environ['FEISHU_APP_SECRET'] = 'fake_secret'
    os.environ['FEISHU_APP_TOKEN'] = 'fake_token'
    os.environ['FEISHU_TABLE_ID'] = 'fake_table'
    os.environ['PASSWORDS'] = json.dumps({"20210001": "pwd1"})

    token_resp = {'code': 0, 'tenant_access_token': 't-fake', 'expire': 7200}
    records_resp = {
        'code': 0,
        'data': {
            'items': [
                {'record_id': 'r1', 'fields': {'username': '20210001', 'room_id': 2, 'seats': '113,114'}},
                {'record_id': 'r2', 'fields': {'username': 'ghost', 'seats': [{'text': '1,2'}]}},
            ],
            'has_more': False,
        }
    }

    demo = _reload_demo()
    fake_requests = types.SimpleNamespace()

    def fake_post(url, **kwargs):
        return types.SimpleNamespace(json=lambda: token_resp)

    def fake_get(url, **kwargs):
        return types.SimpleNamespace(json=lambda: records_resp)

    fake_requests.post = fake_post
    fake_requests.get = fake_get
    demo.requests = fake_requests

    accounts = demo.load_accounts()
    assert len(accounts) == 1, "ghost account has no password, only 1 expected"
    assert accounts[0]['seats'] == [113, 114], "got %s" % accounts[0].get('seats')
    print("[PASS] feishu seats text/list formats parsed into int lists")


def test_13_get_seat_info_reuses_cache():
    print("\n" + "=" * 60)
    print("Test 13: get_seat_info - correct reverse lookup, xlsx parsed once")
    print("=" * 60)
    import pandas as pd

    info = get_seat_info(12920)
    assert info['room'] == '自习室114', "wrong room for 12920, got %s" % info['room']
    assert info['title'] == '113', "wrong seat number for 12920, got %s" % info['title']
    assert info['id'] == '12920'

    # 非法/未知输入要降级成占位，不能让通知本身挂掉
    for bad in (None, 'abc', '', 99999999):
        fallback = get_seat_info(bad)
        assert fallback['room'] == '未知', \
            "bad input %r should degrade to placeholder, got %s" % (bad, fallback)
        assert fallback['id'] == str(bad)

    # 缓存生效：后续查询不应再解析 xlsx（旧实现每次通知都重新 read_excel）
    reads = {'n': 0}
    real_read_excel = pd.read_excel

    def counting_read_excel(*args, **kwargs):
        reads['n'] += 1
        return real_read_excel(*args, **kwargs)

    pd.read_excel = counting_read_excel
    try:
        get_seat_info(12921)
        get_seat_info(12922)
    finally:
        pd.read_excel = real_read_excel

    assert reads['n'] == 0, \
        "cached reverse lookups must not re-parse the workbook, got %d reads" % reads['n']
    print("[PASS] reverse lookup correct, bad input degrades gracefully, workbook parsed once")


def test_14_notification_content_after_seatmap_move():
    print("\n" + "=" * 60)
    print("Test 14: notification content unchanged after get_seat_info moved")
    print("=" * 60)
    import notice

    captured = {}

    class FakeResp:
        def json(self):
            return {'code': 0}

    def fake_post(url, data=None, timeout=None, **kwargs):
        captured['url'] = url
        captured['data'] = data
        return FakeResp()

    real_post = notice.requests.post
    notice.requests.post = fake_post
    try:
        notice.notify('2023xxxx', 2, 12920,
                      {'notification_type': 'wechat', 'sckey': 'FAKEKEY',
                       'begin': 12, 'duration': 9})
    finally:
        notice.requests.post = real_post

    assert 'FAKEKEY' in captured['url'], "sckey must be used in the endpoint"
    content = captured['data']['desp']
    assert '自习室114' in content, "room name missing:\n%s" % content
    assert '座位号: 113' in content, "seat number missing:\n%s" % content
    assert '座位ID: 12920' in content, "seat id missing:\n%s" % content
    assert '12:00 ~ 21:00' in content, "time range wrong:\n%s" % content
    assert '9h' in content, "duration missing:\n%s" % content
    print("[PASS] notification renders room/seat/time correctly via cached lookup")


if __name__ == '__main__':
    tests = [
        test_1_seat_map_structure,
        test_2_known_mapping,
        test_3_unique_title_per_room,
        test_4_resolve_valid,
        test_5_resolve_mixed_invalid,
        test_6_resolve_all_invalid_returns_empty,
        test_7_resolve_invalid_room,
        test_8_account_seats_beat_default_seat_ids,
        test_9_same_level_ids_win_with_warning,
        test_10_default_level_resolution,
        test_11_main_loop_seats_end_to_end,
        test_12_feishu_seats_field,
        test_13_get_seat_info_reuses_cache,
        test_14_notification_content_after_seatmap_move,
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
        _clean_env()
        _restore_local_file()
    print("\n" + "=" * 60)
    print("Result: %d passed, %d failed (total %d)" % (passed, failed, len(tests)))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
