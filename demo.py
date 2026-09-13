import json
import os
import yaml
import requests
from zwulib import appoint_zwulib
from notice import notify, notify_fail
from seatmap import resolve_seats

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
    加载账号列表，优先级:
    1. 本地 config/accounts_config.json（本地运行主方式）
    2. 环境变量 ACCOUNTS（老用法后向兼容，单 Secret 存完整 JSON 含密码）
    3. 环境变量 ACCOUNTS_CONFIG + PASSWORDS（明文配置 + 密码映射）
    4. 飞书多维表格 + PASSWORDS（最便捷，手机/网页改配置）
    """
    # 1. 本地文件（本地运行主方式，不变）
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if isinstance(cfg, list):
            return cfg
        return cfg.get('accounts', [])

    # 2. 老用法：ACCOUNTS 单 Secret（后向兼容，保留）
    accounts_json = os.environ.get('ACCOUNTS', '')
    if accounts_json:
        try:
            data = json.loads(accounts_json)
            if isinstance(data, list):
                print("提示: 检测到 ACCOUNTS Secret（老用法）。如需切换到新用法，"
                      "请删除 ACCOUNTS 并改用 ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)。")
                return data
            if isinstance(data, dict) and 'accounts' in data:
                print("提示: 检测到 ACCOUNTS Secret（老用法）。如需切换到新用法，"
                      "请删除 ACCOUNTS 并改用 ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)。")
                return data['accounts']
        except json.JSONDecodeError:
            print("警告: ACCOUNTS 环境变量 JSON 解析失败")

    # 3. ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)
    accounts_cfg_str = os.environ.get('ACCOUNTS_CONFIG', '')
    if accounts_cfg_str:
        return _load_accounts_split(accounts_cfg_str)

    # 4. 飞书多维表格 + PASSWORDS (Secret)
    feishu_app_id = os.environ.get('FEISHU_APP_ID', '')
    if feishu_app_id:
        return _load_accounts_from_feishu()

    # 都没命中
    print("错误: 未找到账号配置。请使用以下任一方式：")
    print("  1. 本地 config/accounts_config.json")
    print("  2. GitHub Secret ACCOUNTS（老用法）")
    print("  3. GitHub Variable ACCOUNTS_CONFIG + Secret PASSWORDS")
    print("  4. 飞书多维表格 + Secret PASSWORDS（最便捷）")
    return []


def _text_field_value(val):
    """飞书文本字段可能是字符串或 [{"text": "..."}] 列表，统一转成字符串"""
    if isinstance(val, list):
        return ''.join(item.get('text', '') for item in val if isinstance(item, dict))
    return str(val) if val is not None else ''


def _parse_passwords_secret():
    """
    解析 PASSWORDS Secret（{学号: 密码} JSON）。
    未设置或解析失败返回 {}（失败时打印错误）。
    """
    passwords_str = os.environ.get('PASSWORDS', '')
    if not passwords_str:
        return {}
    try:
        passwords = json.loads(passwords_str)
    except json.JSONDecodeError as e:
        print(f"错误: PASSWORDS JSON 解析失败: {e}")
        return {}
    if not isinstance(passwords, dict):
        print("错误: PASSWORDS 必须是 JSON 对象 {学号: 密码}")
        return {}
    return passwords


def _merge_passwords(accounts_cfg, passwords=None, source='PASSWORDS Secret'):
    """
    用 username 查密码映射，合并进账号清单。
    被 _load_accounts_split 和 _load_accounts_from_feishu 共用。

    参数:
        accounts_cfg: 不含密码的账号列表，每个元素必须含 username
        passwords: {username: password} 映射；None 时从 PASSWORDS Secret 解析
        source: 密码来源描述（用于日志）
    返回: 含密码的完整账号列表
    """
    if passwords is None:
        passwords = _parse_passwords_secret()
        if not passwords:
            print("错误: 未设置 PASSWORDS Secret。"
                  "配置分层方式需要配置 PASSWORDS（{学号: 密码} JSON）；"
                  "飞书表格方式也可改用密码表（配置 FEISHU_PASSWORD_TABLE_ID）。")
            return []

    merged = []
    for acc in accounts_cfg:
        username = acc.get('username', '')
        if not username:
            print(f"跳过: 账号配置缺少 username 字段: {acc}")
            continue

        password = passwords.get(username, '')
        if not password:
            print(f"跳过: 账号 {username} 在 {source} 中未找到对应密码")
            continue

        merged_acc = dict(acc)
        merged_acc['password'] = password
        merged.append(merged_acc)

    if not merged:
        print("警告: 没有有效账号（全部密码缺失或缺少 username）")
    return merged


def _load_accounts_split(accounts_cfg_str):
    """
    从 ACCOUNTS_CONFIG (明文 Variable) 读账号清单，从 PASSWORDS (Secret) 查密码。
    返回合并后的账号列表（每个账号含 username + password + 覆盖字段）。

    注意: enabled 停用检查由主循环统一处理，此处不做。
    """
    try:
        accounts_cfg = json.loads(accounts_cfg_str)
    except json.JSONDecodeError as e:
        print(f"错误: ACCOUNTS_CONFIG JSON 解析失败: {e}")
        return []
    if isinstance(accounts_cfg, dict) and 'accounts' in accounts_cfg:
        accounts_cfg = accounts_cfg['accounts']
    if not isinstance(accounts_cfg, list):
        print("错误: ACCOUNTS_CONFIG 必须是 JSON 数组，或含 'accounts' 字段的对象")
        return []

    return _merge_passwords(accounts_cfg)


def _parse_int_list_field(raw):
    """
    解析飞书文本字段为整数列表: "12920,12921" -> [12920, 12921]
    兼容 [{"text": "..."}] 列表格式（飞书文本字段有时返回这种结构）
    """
    if isinstance(raw, list):
        text = ''.join(item.get('text', '') for item in raw if isinstance(item, dict))
    else:
        text = str(raw)
    return [int(s.strip()) for s in text.split(',') if s.strip()]


def _feishu_get_records(headers, app_token, table_id, label='数据表'):
    """
    分页读取一张数据表的全部记录，返回记录列表；失败打印错误并返回 None
    """
    base_url = f'https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records'
    all_records = []
    page_token = ''

    while True:
        params = {'page_size': 500}
        if page_token:
            params['page_token'] = page_token
        try:
            resp = requests.get(base_url, headers=headers, params=params, timeout=30)
            data = resp.json()
        except Exception as e:
            print(f"错误: 读取飞书{label}失败: {e.__class__.__name__}: {e}")
            return None

        if data.get('code') != 0:
            print(f"错误: 读取飞书{label}失败: {data.get('msg', '未知错误')}")
            print("提示: 请检查多维表格是否已给应用授权(可查看权限)，以及 app_token/table_id 是否正确。")
            return None

        items = data.get('data', {}).get('items', [])
        all_records.extend(items)

        if not data.get('data', {}).get('has_more'):
            break
        page_token = data.get('data', {}).get('page_token', '')
        if not page_token:
            break

    return all_records


def _load_feishu_table_passwords(token, app_token):
    """
    读取飞书密码表（可选，配置 FEISHU_PASSWORD_TABLE_ID 后启用），
    返回 {username: password}；未配置或读取失败返回 {}。
    密码表列结构: username（文本）、password（文本），每行一个账号。
    """
    pwd_table_id = os.environ.get('FEISHU_PASSWORD_TABLE_ID', '')
    if not pwd_table_id:
        return {}

    print("飞书: 正在读取密码表...")
    headers = {'Authorization': f'Bearer {token}'}
    records = _feishu_get_records(headers, app_token, pwd_table_id, '密码表')
    if records is None:
        print("警告: 密码表读取失败，将回退使用 PASSWORDS Secret")
        return {}

    passwords = {}
    for record in records:
        fields = record.get('fields', {})
        username = _text_field_value(fields.get('username')).strip()
        password = _text_field_value(fields.get('password')).strip()
        if username and password:
            passwords[username] = password
    print(f"飞书: 密码表读取到 {len(passwords)} 条密码")
    if not passwords:
        print("警告: 密码表中没有有效记录（需要 username 和 password 两列文本字段）")
    return passwords


def _load_accounts_from_feishu():
    """
    从飞书多维表格读取账号配置，密码从飞书密码表读取（可选，
    FEISHU_PASSWORD_TABLE_ID），密码表中没有的账号回退到 PASSWORDS Secret。
    返回合并后的账号列表（每个账号含 username + password + 覆盖字段）。

    需要 4 个环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN, FEISHU_TABLE_ID
    可选环境变量: FEISHU_PASSWORD_TABLE_ID（密码表 table_id，需与账号表同一个多维表格）
    注意: enabled 停用检查由主循环统一处理，此处不做。
    """
    app_id = os.environ.get('FEISHU_APP_ID', '')
    app_secret = os.environ.get('FEISHU_APP_SECRET', '')
    app_token = os.environ.get('FEISHU_APP_TOKEN', '')
    table_id = os.environ.get('FEISHU_TABLE_ID', '')

    missing = [k for k, v in [('FEISHU_APP_ID', app_id), ('FEISHU_APP_SECRET', app_secret),
                              ('FEISHU_APP_TOKEN', app_token), ('FEISHU_TABLE_ID', table_id)]
              if not v]
    if missing:
        print(f"错误: 飞书配置不完整，缺少: {', '.join(missing)}")
        print("请检查 GitHub Secrets 是否已配置这些变量。")
        return []

    # 第一步: 获取 tenant_access_token
    print("飞书: 正在获取 access token...")
    try:
        resp = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': app_id, 'app_secret': app_secret},
            timeout=30
        )
        token_data = resp.json()
    except Exception as e:
        print(f"错误: 获取飞书 token 失败: {e.__class__.__name__}: {e}")
        return []

    if token_data.get('code') != 0:
        print(f"错误: 获取飞书 token 失败: {token_data.get('msg', '未知错误')}")
        return []
    token = token_data['tenant_access_token']

    # 第二步: 分页读取账号配置表记录
    print("飞书: 正在读取多维表格记录...")
    headers = {'Authorization': f'Bearer {token}'}
    all_records = _feishu_get_records(headers, app_token, table_id, '账号配置表')
    if all_records is None:
        return []

    print(f"飞书: 共读取到 {len(all_records)} 条记录")

    # 第三步: 解析字段，转成账号字典
    accounts_cfg = []
    for record in all_records:
        fields = record.get('fields', {})
        username = _text_field_value(fields.get('username')).strip()
        if not username:
            continue

        acc = {'username': username}

        # enabled: 复选框字段返回 bool
        if 'enabled' in fields:
            acc['enabled'] = bool(fields['enabled'])

        # 数字字段: room_id, dday, begin, duration, max-retry
        for key in ('room_id', 'dday', 'begin', 'duration', 'max-retry'):
            val = fields.get(key)
            if val is not None:
                try:
                    acc[key] = int(val)
                except (ValueError, TypeError):
                    acc[key] = val

        # seat_ids / seats: 文本字段 "12920,12921" -> [12920, 12921]
        # seats 是座位号（选座页显示的编号），预约前自动转换为座位ID
        for key in ('seat_ids', 'seats'):
            raw = fields.get(key)
            if raw:
                try:
                    ids = _parse_int_list_field(raw)
                except ValueError:
                    print(f"警告: 飞书表格 {key} 字段含非数字内容，已忽略该字段: {raw}")
                    continue
                if ids:
                    acc[key] = ids

        accounts_cfg.append(acc)

    if not accounts_cfg:
        print("警告: 飞书表格中没有有效账号记录")
        return []

    # 第四步: 合并密码（密码表优先，PASSWORDS Secret 兜底）
    passwords = _parse_passwords_secret()
    table_passwords = _load_feishu_table_passwords(token, app_token)
    if table_passwords:
        passwords = {**passwords, **table_passwords}
    source = ('PASSWORDS Secret 或飞书密码表'
              if os.environ.get('FEISHU_PASSWORD_TABLE_ID') else 'PASSWORDS Secret')
    return _merge_passwords(accounts_cfg, passwords, source)


def _seats_to_ids(account, defaults, seats):
    """座位号查表转换为座位ID，并打印转换结果供确认"""
    room_id = account['room_id'] if account.get('room_id') is not None else defaults.get('room_id')
    seat_ids = resolve_seats(room_id, seats)
    if seat_ids:
        print(f"座位号 {seats} → 座位ID {seat_ids}")
    else:
        print("警告: 所有座位号均无效，将随机选座")
    return seat_ids


def resolve_final_seats(account, defaults):
    """
    决定账号最终使用的 seat_ids（座位号 seats 已查表转换），返回 seat_ids 列表，
    两层都未配置任何座位时返回 None（随机选座）。

    层级: 账号级显式配置 > 默认层（booking_config.yml），
    否则默认层的 seat_ids 会永远挡住账号级的座位号。
    同层同时配置 seat_ids 和 seats 时，以 seat_ids（显式平台ID）为准并警告。
    """
    acc_ids = account.get('seat_ids')
    acc_seats = account.get('seats')

    if acc_ids is not None or acc_seats is not None:
        if acc_ids is not None and acc_seats is not None:
            print(f"警告: 同时配置了 seat_ids={acc_ids} 和 seats={acc_seats}，以 seat_ids 为准")
            return acc_ids
        if acc_ids is not None:
            return acc_ids
        return _seats_to_ids(account, defaults, acc_seats)

    def_ids = defaults.get('seat_ids')
    def_seats = defaults.get('seats')
    if def_ids is not None and def_seats is not None:
        print(f"警告: 同时配置了 seat_ids={def_ids} 和 seats={def_seats}，以 seat_ids 为准")
        return def_ids
    if def_ids is not None:
        return def_ids
    if def_seats is not None:
        return _seats_to_ids(account, defaults, def_seats)
    return None


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

        # 停用开关（所有加载路径统一生效，未配置 enabled 默认为启用）
        if account.get('enabled', True) is False:
            print(f"跳过第 {i} 个账号 {username}: 已停用 (enabled=false)")
            continue

        # 合并: 账号级覆盖 > 默认配置（排除 username/password/enabled 等非预约参数）
        params = {**defaults, **{k: v for k, v in account.items()
                  if k not in ('username', 'password', 'enabled')}}

        # 座位号(seats)查表转换为座位ID(seat_ids)，确定最终预约的座位
        params['seat_ids'] = resolve_final_seats(account, defaults)

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
