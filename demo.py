import json
import os
import random
import time
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor
from zwulib import open_session
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
    'cron-delta-minutes': 5,      # 已废弃，保留兼容
    'max-retry': 32,
    'retry-probe-interval': 10,   # 探路间隔（秒）
    'retry-probe-count': 30,      # 探路次数上限（10s × 30 = 5 分钟探路窗口）
    'retry-rush-interval': 3,     # 猛攻间隔（秒）
    'retry-rush-duration': 60,    # 猛攻持续时长（秒）
    'concurrency': 1,             # 抢座阶段并发账号数（1 = 最保守；3 = 三个一批）
    'concurrency-jitter': 0.8,    # 并发时各账号出手的随机错开上限（秒），0 = 不错开
    'login-retry': 2,             # 登录失败后的额外重试次数（总尝试 = 1 + 该值）
    'login-retry-wait': 3,        # 两次登录尝试之间的等待（秒）
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


def _is_dry_run():
    """
    DRY_RUN=1 时只验证登录链路，不发起任何预约请求（不会占座）。
    用于本地/云端安全地确认登录逻辑没被改坏，并采集真实的登录耗时。
    """
    return os.environ.get('DRY_RUN', '').strip().lower() in ('1', 'true', 'yes', 'on')


def _jitter_seconds(defaults):
    """
    抢座出手的随机错开上限（秒）；0 表示不错开。

    目的是让并发账号的请求在时间上散开，而不是集中落在同一瞬间。
    """
    try:
        return max(0.0, float(defaults.get('concurrency-jitter', 0.8)))
    except (TypeError, ValueError):
        return 0.0


def _friendly_error(exc):
    """把异常翻译成适合写进通知的人话（完整报错仍留在运行日志里）"""
    name = exc.__class__.__name__
    lowered = str(exc).lower()
    if 'webdriver' in name.lower() or 'chrome' in lowered or 'chromedriver' in lowered:
        return '浏览器启动失败'
    if 'timeout' in name.lower():
        return '等待超时，页面响应过慢'
    return f'执行异常（{name}，详细原因见运行日志）'


def _blank_result(username, skipped=False, reason=''):
    """统一的账号结果结构；notified 标记用于保证同一条结果只发一次通知"""
    return {'username': username, 'skipped': skipped, 'skip_reason': reason,
            'session': None, 'params': {}, 'stat': None, 'msg': '',
            'seatid': None, 'notified': False}


def notify_result(result):
    """
    根据结果发通知。同一条结果只发一次（靠 notified 幂等）。

    DRY RUN 下绝不发通知（否则会收到"预约成功"的假消息），只打印一行确认；
    被跳过的账号不通知。
    """
    if result.get('notified') or result['skipped']:
        return
    result['notified'] = True

    username = result['username']
    if _is_dry_run():
        print(f"[dry-run] {username} 登录链路"
              + ("正常" if result['stat'] == 'ok' else f"失败: {result['msg']}"))
        return

    params = result['params']
    if result['stat'] == 'ok':
        try:
            notify(username, params.get('dday', 2), result['seatid'], params)
        except Exception as e:
            print(f"通知发送失败: {e}")
    else:
        try:
            notify_fail(username, result['msg'], params)
        except Exception as e:
            print(f"失败通知发送失败: {e}")


def prepare_account(index, total, account, defaults):
    """
    阶段一：校验 → 参数合并 → 座位解析 → 登录拿轻量会话。

    登录用的浏览器在 open_session 内部随即关闭，因此本阶段结束后只留下会话对象。
    登录失败时 session 为 None 且已填好 stat/msg；单账号异常在此被捕获，
    不会影响其他账号（例如 Chrome 起不来时，后续账号仍会继续登录）。

    注意：本阶段只建立会话，绝不抢座。
    """
    username = account.get('username', '')
    password = account.get('password', '')

    if not username or not password:
        reason = '缺少 username 或 password'
        print(f"跳过第 {index} 个账号: {reason}")
        return _blank_result(username, True, reason)

    # 停用开关（所有加载路径统一生效，未配置 enabled 默认为启用）
    if account.get('enabled', True) is False:
        reason = '已停用 (enabled=false)'
        print(f"跳过第 {index} 个账号 {username}: {reason}")
        return _blank_result(username, True, reason)

    # 合并: 账号级覆盖 > 默认配置（排除 username/password/enabled 等非预约参数）
    params = {**defaults, **{k: v for k, v in account.items()
              if k not in ('username', 'password', 'enabled')}}

    # 座位号(seats)查表转换为座位ID(seat_ids)，确定最终预约的座位
    params['seat_ids'] = resolve_final_seats(account, defaults)

    print(f"\n{'='*40}")
    print(f"登录第 {index}/{total} 个账号: {username}")
    print(f"自习室:{params.get('room_id')} 开始:{params.get('begin')}:00 "
          f"时长:{params.get('duration')}h 座位:{params.get('seat_ids') or '随机'}")
    print(f"{'='*40}")

    result = _blank_result(username)
    result['params'] = params

    try:
        session, err = open_session(
            username, password, params.get('room_id'),
            login_retry=params.get('login-retry', 2),
            login_retry_wait=params.get('login-retry-wait', 3))
    except Exception as e:
        print(f"账号 {username} 执行异常: {e.__class__.__name__}: {e}")
        result['stat'], result['msg'] = 'fail', _friendly_error(e)
        return result

    if session is None:
        result['stat'], result['msg'] = 'fail', err or '登录失败'
        return result

    result['session'] = session
    if _is_dry_run():
        # DRY RUN 只验证登录，结论到此已确定
        result['stat'], result['msg'] = 'ok', 'DRY RUN: 登录成功，未发起预约'
    return result


def book_session(session, params, max_retry=None):
    """
    用轻量会话抢座（只发 HTTP 请求，不需要浏览器）。

    独立成函数是为了让测试能注入替身，而不必真的发请求。
    """
    return session.book(
        params.get('dday'), params.get('begin'), params.get('duration'),
        seat_ids=params.get('seat_ids'),
        max_retry=(params.get('max-retry', 32) if max_retry is None else max_retry),
        probe_interval=params.get('retry-probe-interval', 10),
        probe_count=params.get('retry-probe-count', 30),
        rush_interval=params.get('retry-rush-interval', 3),
        rush_duration=params.get('retry-rush-duration', 60),
    )


def book_one(result, max_retry=None, jitter=0.0):
    """
    阶段二单账号：抢座 → 立即通知。就地更新 result 并返回它。

    jitter（秒）> 0 时先随机等待 0~jitter 再出手。并发只让 3 个请求重叠，
    错开抖动则避免它们落在同一瞬间 —— 时间上散开比"完全同时"更像真人行为。
    """
    username = result['username']

    if jitter > 0:
        delay = random.uniform(0, jitter)
        print(f"[隔离] {username} 随机错开 {delay:.2f}s 后出手")
        time.sleep(delay)

    try:
        stat, msg, seatid = book_session(result['session'], result['params'], max_retry)
    except Exception as e:
        # 单账号异常只影响它自己，其他账号照常处理
        print(f"账号 {username} 抢座异常: {e.__class__.__name__}: {e}")
        stat, msg, seatid = 'fail', _friendly_error(e), None

    result['stat'], result['msg'], result['seatid'] = stat, msg, seatid
    print(f"结果 {username}: {stat} - {msg}")
    notify_result(result)
    return result


def process_account(index, total, account, defaults, max_retry=None):
    """
    单账号完整流程：阶段一 + 阶段二。

    两阶段调度（run_all）是主流程；本函数用于单账号调用与测试，
    走的仍是同一套 prepare_account / book_one，不复制逻辑。
    """
    result = prepare_account(index, total, account, defaults)
    if result['skipped']:
        return result

    if result['session'] is None or _is_dry_run():
        # 登录失败、或 DRY RUN 只验证登录：结论已定，直接通知（内部会跳过发送）
        notify_result(result)
        return result

    return book_one(result, max_retry)


def run_all(accounts, defaults, concurrency=1):
    """
    两阶段调度：

      阶段一 逐个账号登录并产出轻量会话（慢活，浏览器用完立即关闭）
      阶段二 用会话抢座（只发 HTTP 请求），按 concurrency 分批并发

    如此「登录」不占用抢座窗口；并发只发生在最轻的抢座请求上。
    DRY RUN 下只做阶段一，且不发送任何通知。
    """
    total = len(accounts)
    results = [prepare_account(i, total, account, defaults)
               for i, account in enumerate(accounts, 1)]

    # 登录失败的账号结论已定，先通知掉（被跳过的不会通知）
    for r in results:
        if r['session'] is None:
            notify_result(r)

    if _is_dry_run():
        for r in results:
            if r['session'] is not None:
                notify_result(r)
        return results

    ready = [r for r in results if r['session'] is not None]
    if not ready:
        print("\n没有账号成功登录，跳过抢座阶段")
        return results

    workers = max(1, int(concurrency))
    # 并发 > 1 时把各账号的出手时刻随机错开；串行时无需错开
    jitter = _jitter_seconds(defaults) if workers > 1 else 0.0
    suffix = f"，随机错开 0~{jitter:g}s" if jitter > 0 else ""
    print(f"\n登录完成：{len(ready)}/{total} 个账号拿到会话，"
          f"开始抢座（并发 {workers}{suffix}）")
    t_start = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda r: book_one(r, jitter=jitter), ready))
    finally:
        print(f"[timer] 抢座阶段结束，耗时 {time.monotonic() - t_start:.2f}s")
    return results


def print_summary(results, dry_run=False):
    """跑完后的总账，避免出现「某个账号静默消失」时无从察觉"""
    ok = [r for r in results if r['stat'] == 'ok']
    failed = [r for r in results if not r['skipped'] and r['stat'] != 'ok']
    skipped = [r for r in results if r['skipped']]

    print(f"\n{'='*40}")
    if dry_run:
        print(f"DRY RUN 汇总: {len(ok)} 登录成功 / {len(failed)} 失败 / "
              f"{len(skipped)} 跳过 (共 {len(results)} 个账号)")
    else:
        print(f"汇总: {len(ok)} 成功 / {len(failed)} 失败 / {len(skipped)} 跳过 "
              f"(共 {len(results)} 个账号)")
    for r in failed:
        print(f"  失败: {r['username']} - {r['msg']}")
    for r in skipped:
        print(f"  跳过: {r['username']} - {r['skip_reason']}")
    if dry_run:
        print("  本次未发起任何预约请求，也未发送通知")
    print('='*40)


def main():
    accounts = load_accounts()
    defaults = load_booking_config()
    dry_run = _is_dry_run()
    concurrency = defaults.get('concurrency', 1)

    if not accounts:
        print("无账号配置，退出")
        exit(1)

    if dry_run:
        print("=" * 40)
        print("DRY RUN 模式：只验证登录，不发起任何预约请求（不会占座、不发通知）")
        print("=" * 40)

    print(f"共 {len(accounts)} 个账号待处理")

    t_start = time.monotonic()
    results = run_all(accounts, defaults, concurrency=concurrency)

    print_summary(results, dry_run)
    print(f"[timer] 全部账号处理完毕，总耗时 {time.monotonic() - t_start:.2f}s")


if __name__ == '__main__':
    main()
