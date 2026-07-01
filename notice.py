"""
通知模块 - 支持 Server酱（微信）和邮件通知
"""
import requests
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def get_seat_info(seatid):
    """获取座位信息（返回字典，值为标量）"""
    try:
        import pandas as pd
        import os
        xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'zwu_lib.xlsx')
        df = pd.read_excel(xlsx_path, index_col=0)
        matched = df[df['id'] == int(seatid)]
        if matched.empty:
            return {'room': '未知', 'title': str(seatid), 'id': str(seatid)}
        row = matched.iloc[0]
        return {'room': str(row['room']), 'title': str(row['title']), 'id': str(row['id'])}
    except Exception:
        return {'room': '未知', 'title': str(seatid), 'id': str(seatid)}


def notify(user, dday, seatid, config=None):
    """
    发送预约成功通知

    参数:
        user: 用户名/学号
        dday: 延后天数
        seatid: 座位ID
        config: 预约配置字典（含 notification_type, sckey, smtp 等）
    """
    if config is None:
        config = {}

    notification_type = config.get('notification_type', 'none')

    if notification_type == 'wechat':
        _send_wechat(user, dday, seatid, config)
    elif notification_type == 'email':
        _send_email(user, dday, seatid, config)
    else:
        print("未配置通知，跳过")


def notify_fail(user, reason, config=None):
    """
    发送预约失败通知

    参数:
        user: 用户名/学号
        reason: 失败原因
        config: 预约配置字典
    """
    if config is None:
        config = {}

    sckey = config.get('sckey', '')
    notification_type = config.get('notification_type', 'none')

    if notification_type != 'wechat' or not sckey:
        return

    title = f"预约失败 | {user}"
    content = (
        f"**ZWU图书馆助手**\n\n"
        f"- 用户: {user}\n"
        f"- 状态: ❌ 预约失败\n"
        f"- 原因: {reason}\n"
    )

    url = f'https://sctapi.ftqq.com/{sckey}.send'
    try:
        r = requests.post(url, data={'title': title, 'desp': content}, timeout=10)
        if r.json().get("code") == 0 or r.json().get("data", {}).get("error") == 'SUCCESS':
            print("失败通知已发送")
        else:
            print(f"失败通知发送失败: {r.text}")
    except Exception as e:
        print(f"失败通知发送异常: {e}")


def _send_wechat(user, dday, seatid, config):
    """通过 Server酱 推送微信通知"""
    sckey = config.get('sckey', '')
    if not sckey:
        print("未配置 SCKEY，跳过微信通知")
        return

    actual_date = (datetime.now(BEIJING_TZ) + timedelta(days=dday)).strftime('%Y-%m-%d')
    weekday_cn = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday = weekday_cn[(datetime.now(BEIJING_TZ) + timedelta(days=dday)).weekday()]
    seatinfo = get_seat_info(seatid)
    begin = config.get('begin', '?')
    duration = config.get('duration', '?')

    title = f"预约成功 | {user}"
    content = (
        f"**ZWU图书馆助手**\n\n"
        f"- 日期: {actual_date}（{weekday}）\n"
        f"- 时间: {begin}:00 ~ {int(begin) + int(duration)}:00\n"
        f"- 持续时长: {duration}h\n"
        f"- 自习室: {seatinfo['room']}\n"
        f"- 座位号: {seatinfo['title']}\n"
        f"- 座位ID: {seatinfo['id']}\n"
    )

    url = f'https://sctapi.ftqq.com/{sckey}.send'
    try:
        r = requests.post(url, data={'title': title, 'desp': content}, timeout=10)
        if r.json().get("code") == 0 or r.json().get("data", {}).get("error") == 'SUCCESS':
            print("Server酱通知成功")
        else:
            print(f"Server酱通知失败: {r.text}")
    except Exception as e:
        print(f"Server酱通知异常: {e}")


def _send_email(user, dday, seatid, config):
    """通过邮件发送通知"""
    smtp_cfg = config.get('smtp', {})
    smtp_server = smtp_cfg.get('server', 'smtp.office365.com')
    from_addr = smtp_cfg.get('from_addr', '')
    password = smtp_cfg.get('password', '')
    to_addr = smtp_cfg.get('to_addr', '')

    if not all([from_addr, password, to_addr]):
        print("邮件配置不完整，跳过邮件通知")
        return

    actual_date = (datetime.now(BEIJING_TZ) + timedelta(days=dday)).strftime('%Y-%m-%d')
    weekday_cn = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday = weekday_cn[(datetime.now(BEIJING_TZ) + timedelta(days=dday)).weekday()]
    seatinfo = get_seat_info(seatid)
    begin = config.get('begin', '?')
    duration = config.get('duration', '?')

    msg = MIMEText(f'ZWU图书馆助手\n\n'
                   f'日期: {actual_date}（{weekday}）\n'
                   f'时间: {begin}:00 ~ {int(begin) + int(duration)}:00\n'
                   f'持续时长: {duration}h\n'
                   f'自习室: {seatinfo["room"]}\n'
                   f'座位号: {seatinfo["title"]}\n'
                   f'座位ID: {seatinfo["id"]}\n', 'plain', 'utf-8')
    msg['From'] = formataddr(('ZWU图书馆助手', from_addr), charset='utf-8')
    msg['To'] = formataddr(('ZWUbot', to_addr), charset='utf-8')
    msg['Subject'] = f'ZWU图书馆助手_{user}预约成功通知'

    try:
        server = smtplib.SMTP(smtp_server, 587)
        server.starttls()
        server.login(from_addr, password)
        server.sendmail(from_addr, to_addr, msg.as_string())
        server.quit()
        print(f"通知邮件已发送至 {to_addr}")
    except Exception as e:
        print(f"邮件发送失败: {e}")
