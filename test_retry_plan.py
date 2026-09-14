# -*- coding: utf-8 -*-
"""
Retry rhythm (probe -> rush) tests.

Why this matters: the old fixed plan (60s x 20 retries) let a SINGLE failing
account block the whole queue for up to 20 minutes. The plan must therefore be
bounded, must probe gently at first (the script may start before the booking
window opens), then switch to a fast rush.

Also guards a subtle regression: the old code forced max(max_retry, 3), which
made "try exactly once" impossible — the fast first-round sweep needs it.

Run: python test_retry_plan.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from zwulib import (SeatSession, DEFAULT_PROBE_INTERVAL, DEFAULT_PROBE_COUNT,
                    DEFAULT_RUSH_INTERVAL, DEFAULT_RUSH_DURATION)

plan = SeatSession._retry_intervals


def test_1_default_rhythm():
    print("\n" + "=" * 60)
    print("Test 1: default rhythm - probe then rush")
    print("=" * 60)
    intervals = plan(20, DEFAULT_PROBE_INTERVAL, DEFAULT_PROBE_COUNT,
                     DEFAULT_RUSH_INTERVAL, DEFAULT_RUSH_DURATION)
    assert len(intervals) == 20, "expected 20 attempts, got %d" % len(intervals)
    assert intervals[:8] == [30] * 8, \
        "first 8 attempts must probe at 30s, got %s" % intervals[:8]
    assert set(intervals[8:]) <= {3, 0}, \
        "remaining attempts must rush at 3s, got %s" % intervals[8:]
    print("[PASS] 8 probe attempts at 30s, then fast rush at 3s")


def test_2_worst_case_bounded():
    print("\n" + "=" * 60)
    print("Test 2: worst-case block time is bounded")
    print("=" * 60)
    intervals = plan(20, DEFAULT_PROBE_INTERVAL, DEFAULT_PROBE_COUNT,
                     DEFAULT_RUSH_INTERVAL, DEFAULT_RUSH_DURATION)
    budget = sum(intervals)
    old_budget = 60 * 20
    assert budget <= 300, \
        "one account must not block the queue for 5+ minutes, got %ds" % budget
    assert budget < old_budget / 3, \
        "expected a large improvement over the old %ds plan, got %ds" % (old_budget, budget)
    print("[PASS] worst case %ds (old fixed plan: %ds)" % (budget, old_budget))


def test_3_no_trailing_wait():
    print("\n" + "=" * 60)
    print("Test 3: no pointless wait after the final attempt")
    print("=" * 60)
    intervals = plan(20, 30, 8, 3, 60)
    assert intervals[-1] == 0, \
        "the last attempt should not sleep before returning, got %s" % intervals[-1]
    print("[PASS] final interval is 0")


def test_4_single_attempt_supported():
    print("\n" + "=" * 60)
    print("Test 4: max_retry=1 is honoured (fast first-round sweep)")
    print("=" * 60)
    intervals = plan(1, 30, 8, 3, 60)
    assert len(intervals) == 1, \
        "max_retry=1 must mean exactly one attempt, got %d" % len(intervals)
    assert intervals[0] == 0
    print("[PASS] exactly one attempt allowed (old code forced at least 3)")


def test_5_probe_count_clamped():
    print("\n" + "=" * 60)
    print("Test 5: probe count is clamped to max_retry")
    print("=" * 60)
    intervals = plan(3, 30, 99, 3, 60)
    assert len(intervals) == 3, \
        "plan length must never exceed max_retry, got %d" % len(intervals)
    assert intervals[:2] == [30, 30]
    print("[PASS] plan length never exceeds max_retry")


def test_6_rush_bounded_by_duration():
    print("\n" + "=" * 60)
    print("Test 6: rush attempts bounded by rush_duration")
    print("=" * 60)
    intervals = plan(20, 30, 2, 3, 9)  # 9s / 3s -> at most 3 rush attempts
    assert len(intervals) == 5, \
        "expected 2 probe + 3 rush attempts, got %d (%s)" % (len(intervals), intervals)
    print("[PASS] rush attempts bounded by rush_duration/rush_interval")


def test_7_zero_rush_interval_safe():
    print("\n" + "=" * 60)
    print("Test 7: rush_interval=0 does not hang or divide by zero")
    print("=" * 60)
    intervals = plan(10, 30, 5, 0, 60)
    assert len(intervals) > 0, "plan must not be empty"
    assert intervals[-1] == 0
    assert all(i >= 0 for i in intervals), "intervals must never be negative"
    print("[PASS] rush_interval=0 handled safely")


if __name__ == '__main__':
    tests = [
        test_1_default_rhythm,
        test_2_worst_case_bounded,
        test_3_no_trailing_wait,
        test_4_single_attempt_supported,
        test_5_probe_count_clamped,
        test_6_rush_bounded_by_duration,
        test_7_zero_rush_interval_safe,
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
    print("\n" + "=" * 60)
    print("Result: %d passed, %d failed (total %d)" % (passed, failed, len(tests)))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
