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

from seatmap import load_seat_map, resolve_seats


def _reload_demo():
    if 'demo' in sys.modules:
        del sys.modules['demo']
    import demo
    return demo


def _clean_env():
    for k in ('ACCOUNTS', 'ACCOUNTS_CONFIG', 'PASSWORDS',
              'FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_APP_TOKEN', 'FEISHU_TABLE_ID',
              'FEISHU_PASSWORD_TABLE_ID'):
        os.environ.pop(k, None)


def _ensure_no_local_file():
    if os.path.exists(ACCOUNTS_FILE):
        raise RuntimeError("Local file %s exists, will interfere with branch tests" % ACCOUNTS_FILE)


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
    """Mirror of demo.py main loop with mocked appoint/notify."""
    def fake_appoint(username, password, **kwargs):
        captured.append({'username': username, 'seat_ids': kwargs.get('seat_ids'),
                         'room_id': kwargs.get('room_id')})
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
            params['seat_ids'] = demo.resolve_final_seats(account, defaults)
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
