import requests
import yaml
import random
from datetime import datetime, timedelta
import json
import os
from contextlib import contextmanager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import time
import pandas as pd

REQUEST_TIMEOUT = 30  # HTTP请求超时时间（秒）

# 重试节奏：程序可能比系统开放时刻早启动，所以先「探路」（间隔大），
# 之后转「猛攻」（间隔小）抢开抢瞬间。全程不依赖当前时钟。
DEFAULT_PROBE_INTERVAL = 30  # 探路间隔（秒）
DEFAULT_PROBE_COUNT = 8      # 探路次数上限
DEFAULT_RUSH_INTERVAL = 3    # 猛攻间隔（秒）
DEFAULT_RUSH_DURATION = 60   # 猛攻持续时长（秒），用于换算猛攻次数
CANDIDATE_SEAT_INTERVAL = 2  # 同一账号尝试下一个候选座位的间隔（秒）

# 登录重试：站点偶发慢响应会让 WebDriverWait 超时（实测约 10% 概率），
# 但这类失败重跑一次通常就好，不是账号或密码问题。每次重试都重建浏览器实例。
DEFAULT_LOGIN_RETRY = 2      # 登录失败后的额外重试次数（总尝试 = 1 + 该值）
DEFAULT_LOGIN_RETRY_WAIT = 3 # 两次登录尝试之间的等待（秒）
LOGIN_PAGE_TIMEOUT = 15      # 登录页等待超时（秒），给慢网络留缓冲


@contextmanager
def _stage(stages, name):
    """记录一个阶段的耗时并追加到 stages 列表（异常时同样记录）"""
    start = time.monotonic()
    try:
        yield
    finally:
        stages.append((name, time.monotonic() - start))


def _log_timer(username, stages, total, attempts=1):
    """打印分阶段耗时，用于定位瓶颈（优化效果的量化依据）"""
    detail = ' '.join(f"{name}={cost:.2f}s" for name, cost in stages)
    extra = f" 尝试次数={attempts}" if attempts > 1 else ''
    print(f"[timer] user={username} {detail} 合计={total:.2f}s{extra}")

# 自习室编号 -> 名称
ROOM_NAMES = ['自习室112', '自习室113', '自习室114', '自习室212', '自习室213', '自习室214', '自习室312', '自习室313', '自习室314']


def room(room_id):
    """根据编号获取自习室名称"""
    if room_id < 0 or room_id >= len(ROOM_NAMES):
        raise ValueError(f"无效的自习室编号: {room_id}，有效范围: 0-{len(ROOM_NAMES)-1}")
    return ROOM_NAMES[room_id]


def calc_total_seconds(start_time, dday, start_hour):
    """
    计算预约时间的秒数偏移（基于北京时间）。

    start_time 取自 _config.yml，是北京时间 1970-01-01 08:00:00（即 UTC 00:00:00）。
    """
    from datetime import timezone as tz
    now_beijing = datetime.now(tz(timedelta(hours=8)))
    today_0_clock = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
    book_time = today_0_clock + timedelta(days=dday) + timedelta(hours=start_hour)
    start_beijing = (start_time.replace(tzinfo=tz(timedelta(hours=8)))
                     if start_time.tzinfo is None else start_time)
    return int((book_time - start_beijing).total_seconds())


_api_config_cache = None


def load_api_config():
    """
    读取 _config.yml（进程内缓存：start-time / target / headers）

    注意：调用方必须对返回的 headers 做 dict() 复制后再改，
    否则多个账号会共用同一个 dict，Cookie 互相污染。
    """
    global _api_config_cache
    if _api_config_cache is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_config.yml')
        with open(path, 'r', encoding='utf-8-sig') as f_obj:
            _api_config_cache = yaml.safe_load(f_obj)
    return _api_config_cache


class SeatSession:
    """
    登录后的轻量会话：只保留抢座所需的 Cookie、uid 与请求头，不持有浏览器。

    把「登录」与「抢座」拆开的意义：
      - 登录慢（启动浏览器 + 交互，实测 5~18 秒/账号），可以提前全部做完，不占用抢座窗口
      - 抢座只是 HTTP 请求，轻量且可并发，因此浏览器用完即可立即关闭
    """

    def __init__(self, username, cookie, uid, room_id, config=None):
        self.username = username
        self.cookie = cookie
        self.user_data = {'uid': uid}
        self.room_id = room_id
        self.json = None
        self.resp = None

        cfg = config if config is not None else load_api_config()
        self.start_time = cfg['start-time']
        self.book_url = cfg['target']
        self.headers = dict(cfg['headers'])  # 必须复制：缓存对象不能被子类实例改动
        self.headers['Cookie'] = cookie

    def _calc_total_seconds(self, dday, start_hour):
        return calc_total_seconds(self.start_time, dday, start_hour)

    @staticmethod
    def _retry_intervals(max_retry, probe_interval, probe_count,
                         rush_interval, rush_duration):
        """
        生成每次尝试前的等待间隔：前 probe_count 次走探路间隔，之后走猛攻间隔。
        最后一次尝试后不再等待。返回长度不超过 max_retry 的列表。

        例（默认值）：8 次 × 30s + 12 次 × 3s ≈ 4.6 分钟，而旧的固定 60s × 20 次是 20 分钟。
        """
        max_retry = max(1, int(max_retry))
        probe_count = max(0, min(int(probe_count), max_retry))

        intervals = [max(0, probe_interval)] * probe_count

        remaining = max_retry - probe_count
        if remaining > 0 and rush_interval > 0:
            rush_slots = max(1, int(rush_duration // rush_interval))
            intervals.extend([rush_interval] * min(remaining, rush_slots))

        if not intervals:
            intervals = [0]

        intervals[-1] = 0  # 最后一次不再等待
        return intervals

    def _book_random_seat(self, total_seconds, duration):
        """搜索可用座位并随机选一个（纯 HTTP，不需要浏览器）"""
        seat_url = 'https://zjwu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1'
        tmpdata = f"beginTime={total_seconds}&duration={3600 * duration}&num=1&space_category%5Bcategory_id%5D=591&space_category%5Bcontent_id%5D=11"

        tmpresp = requests.post(seat_url, data=tmpdata, headers=self.headers, timeout=REQUEST_TIMEOUT)
        tmpjson = json.loads(tmpresp.text)

        df = pd.DataFrame(columns=['room', 'id', 'title', 'ava'])
        idx = 0
        totalSeatInfo = tmpjson['allContent']['children'][2]['children']['children']
        for i in totalSeatInfo:
            x = i['roomName']
            for j in i['seatMap']['POIs']:
                df.loc[idx] = [x, j['id'], j['title'], j['state']]
                idx += 1
        df['id'] = df['id'].astype('int')
        df['title'] = df['title'].astype('int')
        df['ava'] = df['ava'].astype('int')

        df = df[(df['room'] == room(self.room_id)) & (df['ava'] == 0) & (df['title'] % 2 == 0)]
        print(f"可用座位: {len(df)} 个")

        if df.empty:
            return 'fail', '无可用座位', None

        seat = random.choice(list(df['id']))
        data = f"beginTime={total_seconds}&duration={3600 * duration}&seats[0]={seat}&seatBookers[0]={self.user_data['uid']}"

        self.resp = requests.post(self.book_url, data=data, headers=self.headers, timeout=REQUEST_TIMEOUT)
        self.json = json.loads(self.resp.text)
        return self.json["CODE"], self.json["MESSAGE"] + " 座位:{}".format(seat), seat

    def _book_specific_seats(self, total_seconds, duration, seat_ids):
        """预约指定的座位，成功后立即停止，重复则尝试下一个"""
        code, msg, seat_id = 'fail', '无指定座位', None
        for seat_id in seat_ids:
            data = f"beginTime={total_seconds}&duration={3600 * duration}&seats[0]={seat_id}&seatBookers[0]={self.user_data['uid']}"
            print(f"预约座位 ID:{seat_id}")
            resp = requests.post(self.book_url, data=data, headers=self.headers, timeout=REQUEST_TIMEOUT)
            result = json.loads(resp.text)
            code = result.get("CODE")
            msg = result.get("MESSAGE", "")
            print(f"  结果: {code} - {msg}")

            # 预约成功，立即返回
            if code == "ok":
                return "ok", f"预约成功 座位:{seat_id}", seat_id

            time.sleep(CANDIDATE_SEAT_INTERVAL)  # 避免请求太频繁

        # 所有座位都试过了，返回最后一个结果
        return code, msg, seat_id

    def book(self, dday, start_hour, duration, seat_ids=None, max_retry=20,
             probe_interval=DEFAULT_PROBE_INTERVAL,
             probe_count=DEFAULT_PROBE_COUNT,
             rush_interval=DEFAULT_RUSH_INTERVAL,
             rush_duration=DEFAULT_RUSH_DURATION):
        """
        按「探路 → 猛攻」节奏重试抢座，返回 (stat, msg, seatid)

        seat_ids 为 None 或空时随机选座；否则按顺序尝试每个候选座位。
        """
        total_seconds = self._calc_total_seconds(dday, start_hour)
        intervals = self._retry_intervals(max_retry, probe_interval, probe_count,
                                          rush_interval, rush_duration)

        stat, msg, seatid = 'fail', '未尝试', None
        for attempt, interval in enumerate(intervals):
            try:
                print(f"\n--- 第 {attempt + 1}/{len(intervals)} 次尝试 ---")

                # 如果指定了座位ID，直接预约
                if seat_ids:
                    stat, msg, seatid = self._book_specific_seats(total_seconds, duration, seat_ids)
                else:
                    stat, msg, seatid = self._book_random_seat(total_seconds, duration)

                # 成功或重复预约，立即返回
                if stat == "ok" or '请勿重复预约' in msg:
                    return stat, msg, seatid

                if interval > 0:
                    print(f"预约失败: {msg}，{interval}秒后重试...")
                    time.sleep(interval)

            except Exception as e:
                print(f"第 {attempt + 1} 次尝试异常: {e.__class__.__name__}: {e}")
                if interval > 0:
                    time.sleep(interval)

        # 所有重试用完
        return stat, msg, seatid


class SeatAutoBooker:
    """负责用浏览器登录并取到 uid，随后交由 SeatSession 完成抢座"""

    def __init__(self, userID, userPass, room_id, seat_ids=None):
        self.json = None
        self.resp = None
        self.user_data = {}
        self._begin_hour = 12  # 初始化默认值

        self.un = userID  # 学号
        print("使用用户：{}".format(self.un))
        self.password = userPass  # 密码
        self.seat_ids = seat_ids  # 指定座位ID列表，None则随机选

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-extensions')

        # 优先使用系统 chromedriver（CI 环境），否则用 webdriver-manager 自动下载
        import shutil
        if shutil.which('chromedriver'):
            self.driver = webdriver.Chrome(options=chrome_options)
        else:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.driver, LOGIN_PAGE_TIMEOUT, 0.5)
        self.cookie = None

        cfg = load_api_config()
        self.start_time = cfg['start-time']
        self.book_url = cfg['target']
        self.headers = dict(cfg['headers'])  # 复制，避免多个实例共用同一 dict
        self.room_id = room_id

    def to_session(self):
        """把登录后的状态转成轻量会话，之后即可关闭浏览器（抢座不再需要它）"""
        return SeatSession(self.un, self.cookie, self.user_data['uid'], self.room_id)

    def login(self):
        """登录智数图平台"""
        pwd_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[2]/div/div[3]/div/div[2]/input"""
        button_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[3]"""

        try:
            self.driver.get("https://zjwu.huitu.zhishulib.com/")

            # 找到用户名输入框（wait.until 本身就在等页面就绪，无需额外固定等待）
            self.wait.until(EC.presence_of_element_located((By.NAME, "login_name")))
            self.driver.find_element(By.NAME, 'login_name').clear()
            self.driver.find_element(By.NAME, 'login_name').send_keys(self.un)

            # 找到密码输入框
            self.wait.until(EC.presence_of_element_located((By.XPATH, pwd_path_selector)))
            self.driver.find_element(By.XPATH, pwd_path_selector).clear()
            self.driver.find_element(By.XPATH, pwd_path_selector).send_keys(self.password)

            # 找到登录按钮并点击
            self.wait.until(EC.presence_of_element_located((By.XPATH, button_path_selector)))
            self.driver.find_element(By.XPATH, button_path_selector).click()

            # 等跳出登录页，而不是固定睡 8 秒。
            # 站点若改成不更换 URL 的路由方式，可设 LOGIN_WAIT_MODE=fixed 回退到旧的固定等待。
            if os.environ.get('LOGIN_WAIT_MODE', 'url').lower() == 'fixed':
                time.sleep(8)
            else:
                self.wait.until(lambda d: 'login' not in (d.current_url or '').lower())

            # 提取cookies
            cookie_list = self.driver.get_cookies()
            self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
            self.headers['Cookie'] = self.cookie

            # 验证登录是否成功
            current_url = self.driver.current_url
            if 'login' in current_url.lower():
                print("登录可能失败，URL仍为登录页")
                return -1

        except TimeoutException:
            print("登录超时，仍未跳出登录页")
            return -1
        except Exception as e:
            # 带上真实原因，否则一旦环境/站点变化，排查只能靠猜
            detail = ' '.join(str(e).split())[:200]
            print(f"登录异常（{e.__class__.__name__}）: {detail}")
            return -1
        return 0

    def get_user_info(self):
        """获取用户UID"""
        headers = self.headers.copy()
        headers['Cookie'] = self.cookie
        try:
            resp = requests.post("https://zjwu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1",
                                headers=headers, data="", timeout=REQUEST_TIMEOUT)
            self.user_data = resp.json()['DATA']
            _ = self.user_data['uid']
        except Exception as e:
            print(f"获取用户数据失败: {e.__class__.__name__}")
            return -1
        print("获取用户数据成功")
        return 0


def open_session(username, password, room_id, login_retry=DEFAULT_LOGIN_RETRY,
                 login_retry_wait=DEFAULT_LOGIN_RETRY_WAIT):
    """
    启动浏览器 → 登录 → 取 UID → 产出轻量会话，随后**立即关闭浏览器**。

    返回 (session, err_msg)：成功时 err_msg 为 None，登录失败时 session 为 None。
    构造阶段的异常（如浏览器起不来）向外抛出，由调用方统一转成可读文案并做逐账号隔离。

    登录是这里最脆弱的一环：实测约 10% 概率因站点慢响应而超时，但重跑通常就能成功，
    并非账号或密码问题（已用同一账号重跑验证）。因此登录/取UID 失败会在**全新浏览器实例**
    上重试 login_retry 次；构造异常（浏览器起不来）不重试 —— 那通常是环境问题，重试只会更慢。
    """
    t_start = time.monotonic()
    stages = []
    last_err = '登录失败'

    for attempt in range(max(0, int(login_retry)) + 1):
        s = None
        try:
            if attempt > 0:
                print(f"登录重试 {attempt}/{login_retry}（上次失败原因: {last_err}）")
                time.sleep(max(0.0, float(login_retry_wait)))

            with _stage(stages, '启动'):
                s = SeatAutoBooker(username, password, room_id, None)

            with _stage(stages, '登录'):
                if s.login() != 0:
                    last_err = '登录失败'
                    _close(s)
                    continue

            with _stage(stages, '取UID'):
                if s.get_user_info() != 0:
                    last_err = '获取用户信息失败'
                    _close(s)
                    continue

            with _stage(stages, '转会话'):
                session = s.to_session()
            _close(s)
            _log_timer(username, stages, time.monotonic() - t_start,
                       attempts=attempt + 1)
            return session, None

        except Exception:
            # 构造阶段异常（浏览器起不来等）：关闭已开实例后向上抛，不重试
            _close(s)
            _log_timer(username, stages, time.monotonic() - t_start,
                       attempts=attempt + 1)
            raise

    # 所有尝试都失败
    _log_timer(username, stages, time.monotonic() - t_start,
               attempts=max(0, int(login_retry)) + 1)
    return None, last_err or '登录失败'


def _close(booker):
    """安全关闭浏览器，收尾失败不应影响主流程结论"""
    if booker is None:
        return
    try:
        booker.driver.quit()
    except Exception:
        pass


def appoint_zwulib(username, password, room_id=2, dday=2, begin=12, duration=9,
                   seat_ids=None, cron_delta_minutes=5, max_retry=20,
                   probe_interval=DEFAULT_PROBE_INTERVAL,
                   probe_count=DEFAULT_PROBE_COUNT,
                   rush_interval=DEFAULT_RUSH_INTERVAL,
                   rush_duration=DEFAULT_RUSH_DURATION,
                   dry_run=False):
    """
    单账号一步到位：登录 + 抢座（内部走 open_session + SeatSession.book）。

    dry_run=True 时登录成功后即返回，不发起任何预约请求（不会占座）。
    返回: (stat, msg, seatid)
    """
    session, err = open_session(username, password, room_id)
    if session is None:
        return 'fail', err or '登录失败', None

    if dry_run:
        print("DRY RUN: 登录成功，跳过预约（未发起任何预约请求）")
        return 'ok', 'DRY RUN: 登录成功，未发起预约', None

    stat, msg, seatid = session.book(
        dday, begin, duration, seat_ids=seat_ids, max_retry=max_retry,
        probe_interval=probe_interval, probe_count=probe_count,
        rush_interval=rush_interval, rush_duration=rush_duration)
    print(stat, msg)
    return stat, msg, seatid
