import requests
import yaml
import random
from datetime import datetime, timedelta
import json
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import time
import pandas as pd

REQUEST_TIMEOUT = 30  # HTTP请求超时时间（秒）

# 自习室编号 -> 名称
ROOM_NAMES = ['自习室112', '自习室113', '自习室114', '自习室212', '自习室213', '自习室214', '自习室312', '自习室313', '自习室314']


def room(room_id):
    """根据编号获取自习室名称"""
    if room_id < 0 or room_id >= len(ROOM_NAMES):
        raise ValueError(f"无效的自习室编号: {room_id}，有效范围: 0-{len(ROOM_NAMES)-1}")
    return ROOM_NAMES[room_id]


class SeatAutoBooker:
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
        self.wait = WebDriverWait(self.driver, 10, 0.5)
        self.cookie = None

        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_config.yml')
        with open(config_path, 'r', encoding='utf-8-sig') as f_obj:
            cfg = yaml.safe_load(f_obj)
            self.start_time = cfg['start-time']
            self.book_url = cfg['target']
            self.headers = cfg['headers']
            self.room_id = room_id

    def _calc_total_seconds(self, dday, start_hour):
        """计算预约时间的秒数偏移（基于北京时间）"""
        from datetime import timezone as tz
        now_beijing = datetime.now(tz(timedelta(hours=8)))
        today_0_clock = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
        book_time = today_0_clock + timedelta(days=dday) + timedelta(hours=start_hour)
        # start_time 是北京时间 1970-01-01 08:00:00（即 UTC 00:00:00）
        start_beijing = self.start_time.replace(tzinfo=tz(timedelta(hours=8))) if self.start_time.tzinfo is None else self.start_time
        delta = book_time - start_beijing
        return int(delta.total_seconds())

    def book_favorite_seat(self, dday, start_hour, duration, cron_delta_minutes=5, max_retry=20):
        """
        在预约时间窗口内循环重试预约座位

        参数:
            dday: 延后天数
            start_hour: 开始时间小时
            duration: 持续时长
            cron_delta_minutes: 提前几分钟开始尝试
            max_retry: 最大重试次数
        """
        # 防止无效参数
        max_retry = max(max_retry, 3)

        total_seconds = self._calc_total_seconds(dday, start_hour)
        retry_interval = 60  # 重试间隔60秒

        stat, msg, seatid = 'fail', '未尝试', None
        for attempt in range(max_retry):
            try:
                print(f"\n--- 第 {attempt + 1}/{max_retry} 次尝试 ---")

                # 如果指定了座位ID，直接预约
                if self.seat_ids:
                    stat, msg, seatid = self._book_specific_seats(total_seconds, duration)
                else:
                    stat, msg, seatid = self._book_random_seat(total_seconds, duration)

                # 成功或重复预约，立即返回
                if stat == "ok" or '请勿重复预约' in msg:
                    return stat, msg, seatid

                print(f"预约失败: {msg}，{retry_interval}秒后重试...")
                time.sleep(retry_interval)

            except Exception as e:
                print(f"第 {attempt + 1} 次尝试异常: {e.__class__.__name__}: {e}")
                time.sleep(retry_interval)

        # 所有重试用完
        return stat, msg, seatid

    def _book_random_seat(self, total_seconds, duration):
        """搜索可用座位并随机选一个"""
        seat_url = 'https://zjwu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1'
        tmpdata = f"beginTime={total_seconds}&duration={3600 * duration}&num=1&space_category%5Bcategory_id%5D=591&space_category%5Bcontent_id%5D=11"

        headers = self.headers.copy()
        headers['Cookie'] = self.cookie
        tmpresp = requests.post(seat_url, data=tmpdata, headers=headers, timeout=REQUEST_TIMEOUT)
        tmpjson = json.loads(tmpresp.text)

        df = pd.DataFrame(columns=['room', 'id', 'title', 'ava'])
        idx = 0
        totalSeatInfo = tmpjson['allContent']['children'][2]['children']['children']
        for i in totalSeatInfo:
            x = i['roomName']
            for j in i['seatMap']['POIs']:
                y = j['id']
                z = j['title']
                ava = j['state']
                df.loc[idx] = [x, y, z, ava]
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

        headers = self.headers.copy()
        headers['Cookie'] = self.cookie
        self.resp = requests.post(self.book_url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
        self.json = json.loads(self.resp.text)
        return self.json["CODE"], self.json["MESSAGE"] + " 座位:{}".format(seat), seat

    def _book_specific_seats(self, total_seconds, duration):
        """预约指定的座位，成功后立即停止，重复则尝试下一个"""
        code, msg, seat_id = 'fail', '无指定座位', None
        for seat_id in self.seat_ids:
            data = f"beginTime={total_seconds}&duration={3600 * duration}&seats[0]={seat_id}&seatBookers[0]={self.user_data['uid']}"
            headers = self.headers.copy()
            headers['Cookie'] = self.cookie
            print(f"预约座位 ID:{seat_id}")
            resp = requests.post(self.book_url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
            result = json.loads(resp.text)
            code = result.get("CODE")
            msg = result.get("MESSAGE", "")
            print(f"  结果: {code} - {msg}")

            # 预约成功，立即返回
            if code == "ok":
                return "ok", f"预约成功 座位:{seat_id}", seat_id

            time.sleep(5)  # 避免请求太频繁

        # 所有座位都试过了，返回最后一个结果
        return code, msg, seat_id

    def login(self):
        """登录智数图平台"""
        pwd_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[2]/div/div[3]/div/div[2]/input"""
        button_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[3]"""

        try:
            self.driver.get("https://zjwu.huitu.zhishulib.com/")
            time.sleep(3)

            # 找到用户名输入框
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
            time.sleep(8)

            # 提取cookies
            cookie_list = self.driver.get_cookies()
            self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
            self.headers['Cookie'] = self.cookie

            # 验证登录是否成功
            current_url = self.driver.current_url
            if 'login' in current_url.lower():
                print("登录可能失败，URL仍为登录页")
                return -1

        except Exception as e:
            print(e.__class__.__name__ + "无法登录")
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


def appoint_zwulib(username, password, room_id=3, dday=1, begin=8, duration=13,
                    seat_ids=None, cron_delta_minutes=5, max_retry=20):
    """
    预约图书馆座位（带时间窗口和重试）

    返回: (stat, msg, seatid)
    """
    s = SeatAutoBooker(username, password, room_id, seat_ids)
    try:
        if s.login() != 0:
            return 'fail', '登录失败', None
        if s.get_user_info() != 0:
            return 'fail', '获取用户信息失败', None

        stat, msg, seatid = s.book_favorite_seat(dday, begin, duration,
                                                  cron_delta_minutes, max_retry)
        print(stat, msg)
        return stat, msg, seatid
    finally:
        s.driver.quit()
