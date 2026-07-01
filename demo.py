import json
import os
import yaml
from zwulib import appoint_zwulib
from notice import notify, notify_fail

# ============================================
# 配置加载：账号从 accounts_config.json 读取，预约参数从 booking_config.yml 读取
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')
BOOKING_CONFIG_FILE = os.path.join(BASE_DIR, 'config', 'booking_config.yml')

# 内联默认值（兜底，booking_config.yml 也不存在时使用）
DEFAULTS = {
    'room_id': 2,
    'dday': 2,
    'begin': 12,
    'duration': 9,
    'seat_ids': [12920, 12921],
    'cron-delta-minutes': 5,
    'max-retry': 20,
    'notification_type': 'none',
    'sckey': '',
    'smtp': {},
}


def load_accounts():
    """
    加载账号列表:
    1. 本地 config/accounts_config.json（主方式）
    2. 环境变量 ACCOUNTS（可选 fallback）
    """
    # 优先从本地文件读取
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if isinstance(cfg, list):
            return cfg
        return cfg.get('accounts', [])

    # fallback: 环境变量
    accounts_json = os.environ.get('ACCOUNTS', '')
    if accounts_json:
        try:
            data = json.loads(accounts_json)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and 'accounts' in data:
                return data['accounts']
        except json.JSONDecodeError:
            print("警告: ACCOUNTS 环境变量 JSON 解析失败")

    print("错误: 未找到账号配置，请创建 config/accounts_config.json")
    return []


def load_booking_config():
    """
    加载默认预约参数（从仓库配置文件）
    """
    config = DEFAULTS.copy()

    if os.path.exists(BOOKING_CONFIG_FILE):
        with open(BOOKING_CONFIG_FILE, 'r', encoding='utf-8') as f:
            yml_cfg = yaml.safe_load(f) or {}
        # 加载所有配置项，不只限于 DEFAULTS 中的 key
        config.update(yml_cfg)

    # SCKEY 优先从环境变量读取（GitHub Secrets 覆盖）
    sckey = os.environ.get('SCKEY', '')
    if sckey:
        config['sckey'] = sckey

    # 有 SCKEY 但没配通知类型时，自动启用微信通知
    if config.get('sckey') and config.get('notification_type') == 'none':
        config['notification_type'] = 'wechat'

    return config


if __name__ == '__main__':
    accounts = load_accounts()
    defaults = load_booking_config()

    if not accounts:
        print("无账号配置，退出")
        exit(1)

    print(f"共 {len(accounts)} 个账号待预约")

    for i, account in enumerate(accounts, 1):
        username = account.get('username', '')
        password = account.get('password', '')

        if not username or not password:
            print(f"跳过第 {i} 个账号: 缺少 username 或 password")
            continue

        # 合并: 账号级覆盖 > 默认配置
        params = {**defaults, **{k: v for k, v in account.items()
                  if k not in ('username', 'password')}}

        print(f"\n{'='*40}")
        print(f"预约第 {i}/{len(accounts)} 个账号: {username}")
        print(f"自习室:{params.get('room_id')} 开始:{params.get('begin')}:00 "
              f"时长:{params.get('duration')}h 座位:{params.get('seat_ids') or '随机'}")
        print(f"{'='*40}")

        stat, msg, seatid = appoint_zwulib(
            username, password,
            room_id=params.get('room_id'),
            dday=params.get('dday'),
            begin=params.get('begin'),
            duration=params.get('duration'),
            seat_ids=params.get('seat_ids'),
            cron_delta_minutes=params.get('cron-delta-minutes', 5),
            max_retry=params.get('max-retry', 20),
        )

        # 预约后通知
        if stat == "ok":
            try:
                notify(username, params.get('dday', 2), seatid, params)
            except Exception as e:
                print(f"通知发送失败: {e}")
        else:
            # 预约失败，发送失败原因
            try:
                notify_fail(username, msg, params)
            except Exception as e:
                print(f"失败通知发送失败: {e}")
