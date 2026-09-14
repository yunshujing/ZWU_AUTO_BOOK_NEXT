# -*- coding: utf-8 -*-
"""
座位号 → 座位ID 映射与转换

数据来源 zwu_lib.xlsx，列结构: (索引, room, id, title)
- room:  自习室名称（如 自习室114），顺序与 zwulib.ROOM_NAMES 一致
- id:    平台座位ID（预约接口使用，配置 seat_ids 填的就是它）
- title: 座位号（选座页显示的编号，每个自习室内唯一，从 1 开始）

命令行查询:
    python seatmap.py <自习室编号>              打印该自习室全部 座位号→座位ID
    python seatmap.py <自习室编号> <座位号>...  查询指定座位号对应的座位ID
    python seatmap.py                           显示自习室编号对照
"""
import os
import sys

import pandas as pd

from zwulib import ROOM_NAMES, room

XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'zwu_lib.xlsx')

_seat_map_cache = None  # {自习室编号: {座位号: 座位ID}}，进程内只读一次表


def load_seat_map():
    """加载 zwu_lib.xlsx 座位映射表，返回 {自习室编号: {座位号: 座位ID}}"""
    global _seat_map_cache
    if _seat_map_cache is not None:
        return _seat_map_cache
    df = pd.read_excel(XLSX_PATH, index_col=0)
    seat_map = {}
    for room_id, room_name in enumerate(ROOM_NAMES):
        sub = df[df['room'] == room_name]
        seat_map[room_id] = dict(zip(sub['title'].astype(int), sub['id'].astype(int)))
    _seat_map_cache = seat_map
    return _seat_map_cache


def resolve_seats(room_id, seat_numbers):
    """
    把座位号列表转换为座位ID列表

    座位号不是数字或在该自习室不存在（如自习室114无93-102号）时，打印警告并跳过；
    全部无效时返回空列表（调用方按未指定座位处理，回退随机选座）。
    """
    seat_map = load_seat_map()
    if room_id is None or room_id not in seat_map:
        print(f"警告: 无效的自习室编号 {room_id}，座位号 {seat_numbers} 无法转换")
        return []

    room_map = seat_map[room_id]
    seat_ids = []
    for no in seat_numbers:
        try:
            no = int(no)
        except (ValueError, TypeError):
            print(f"警告: {room(room_id)} 座位号 '{no}' 不是数字，已跳过")
            continue
        seat_id = room_map.get(no)
        if seat_id is None:
            print(f"警告: {room(room_id)} 无座位号 {no}（座位号范围 1-{max(room_map)}），已跳过")
        else:
            seat_ids.append(seat_id)
    return seat_ids


_seat_info_index_cache = None  # {座位ID: (自习室名, 座位号)}，进程内只建一次


def _build_seat_info_index():
    """基于 load_seat_map() 的缓存构建「座位ID → (自习室名, 座位号)」反查索引"""
    global _seat_info_index_cache
    if _seat_info_index_cache is None:
        index = {}
        for room_id, room_map in load_seat_map().items():
            room_name = ROOM_NAMES[room_id]
            for title, seat_id in room_map.items():
                index[seat_id] = (room_name, title)
        _seat_info_index_cache = index
    return _seat_info_index_cache


def get_seat_info(seat_id):
    """
    座位ID → {'room', 'title', 'id'}（通知模块使用）

    复用 load_seat_map() 的进程内缓存，避免每次发通知都重新解析 xlsx；
    非法输入或座位表不可读时降级为「未知」占位，不影响通知本身。
    """
    fallback = {'room': '未知', 'title': str(seat_id), 'id': str(seat_id)}
    try:
        key = int(seat_id)
    except (TypeError, ValueError):
        return fallback
    try:
        matched = _build_seat_info_index().get(key)
    except Exception:
        # 座位表读不出来时不能让通知本身挂掉
        return fallback
    if matched is None:
        return fallback
    room_name, title = matched
    return {'room': room_name, 'title': str(title), 'id': str(key)}


def main():
    if len(sys.argv) < 2:
        print("用法: python seatmap.py <自习室编号> [座位号...]")
        print("例:   python seatmap.py 2 113    查自习室114的113号座位对应的ID")
        print("      python seatmap.py 2        打印自习室114全部座位映射")
        print("\n自习室编号对照:")
        for i, name in enumerate(ROOM_NAMES):
            print(f"  {i} = {name}")
        sys.exit(1)

    room_id = int(sys.argv[1])
    seat_map = load_seat_map()
    if room_id not in seat_map:
        print(f"错误: 无效的自习室编号 {room_id}，有效范围 0-{len(ROOM_NAMES) - 1}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        # 指定座位号查询（复用预约时的同一条转换逻辑，无效项会打印警告）
        seat_ids = resolve_seats(room_id, sys.argv[2:])
        print(f"{room(room_id)} 座位号 {sys.argv[2:]} → 座位ID {seat_ids}")
    else:
        # 打印该自习室全部映射
        for no in sorted(seat_map[room_id]):
            print(f"{room(room_id)} 座位号 {no} → 座位ID {seat_map[room_id][no]}")


if __name__ == '__main__':
    main()
