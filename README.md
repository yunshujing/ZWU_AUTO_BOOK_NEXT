![ZWU_AUTO_BOOK_NEXT_logo](image/readme/ZWU_AUTO_BOOK_NEXT_logo.png)

# ZWU AUTO BOOK NEXT浙江万里学院图书馆自动预约脚本

> ***"当笔墨铺就的栈道缓缓延伸，你伏案笃学沉淀的恒心自会成帆，载你走遍万里风光的求索征途。"***

> [!IMPORTANT]
> 请遵守以下使用协议，若不同意此协议，请移步其它项目
>
> <details><summary>使用协议</summary>
>
> - 本项目仅供学术交流使用，作者不对任何因使用本脚本造成的后果负责
> - 请合理使用，切勿占用公共资源（预约但不去签到等行为）
> - 滥用脚本可能导致封号、账号锁定等后果
> - 本项目将停止维护并将被移除，当发生以下情况之一:
>   - 本项目被浙江万里学院图书馆或校方要求删除
>   - 作者发现本项目影响到了图书馆正常的预约服务
>   - 作者发现本项目被滥用或有其他不妥之处
> - 当本项目被移除后，请各位使用者自觉停止使用 fork 的代码，以免造成不必要的麻烦。
>
> </details>

> [!NOTE]
> 本项目基于 [ZWU_AUTO_Booking](https://github.com/ZWUTA/ZWU_AUTO_Booking) 进行重构与功能扩展。

---

## 📑 目录

- [✨ 功能特性](#-功能特性)
- [🚀 快速开始](#-快速开始github-actions-自动预约)
- [📋 自习室编号对照](#-自习室编号对照)
- [📦 配置参数说明](#-配置参数说明)
- [💬 通知配置](#-通知配置)
- [🖥️ 本地化部署](#️-本地化部署)
- [📁 文件结构](#-文件结构)
- [🙏 致谢](#-致谢)
- [📄 开源协议](#-开源协议)

---

## ✨ 功能特性

- 🔐 **自动登录** — Selenium headless Chrome，无需手动操作
- 🪑 **智能选座** — 自动搜索可用座位，优先偶数编号（假设有插座）
- 🎯 **指定座位** — 支持指定座位 ID，按优先级依次尝试
- ⏰ **手动/定时预约** — 支持手动触发，也可配置 GitHub Actions 定时触发
- 💬 **微信通知** — Server酱推送预约结果（成功含日期时间座位，失败含原因）
- 👥 **多账号管理** — 支持多账号，每个账号可单独配置参数
- 🖥️ **本地运行** — 支持本地直接运行，无需依赖外部服务

---

## 🚀 快速开始（GitHub Actions 自动预约）

### 1. Fork 仓库

点击右上角 **Fork**，将仓库复制到你的 GitHub 账号下。

### 2. 配置账号

进入仓库 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加：

| 环境变量名   | 说明                        |
| ------------ | --------------------------- |
| `ACCOUNTS` | 账号列表 JSON（见下方格式） |
| `SCKEY`    | Server酱推送 Key（可选）    |

`ACCOUNTS` 的值格式（直接粘贴 JSON）：

**单账号：**

```json
[
    {
        "username": "你的学号",
        "password": "你的密码"
    }
]
```

**多账号：**

```json
[
    {
        "username": "学号1",
        "password": "密码1"
    },
    {
        "username": "学号2",
        "password": "密码2",
        "room_id": 4,
        "begin": 21
    }
]
```

> [!TIP]
> 每个账号可单独覆盖 `room_id`、`begin`、`duration`、`seat_ids` 等参数，未填写的使用 `booking_config.yml` 默认值。全部可覆盖字段见 `config/accounts_config.example.json`。

### 3. 配置预约参数

编辑 `config/booking_config.yml`：

```yaml
room_id: 2        # 自习室编号（0-8）
dday: 2           # 延后天数（2=后天）
begin: 12         # 开始时间（12=中午12点）
duration: 9       # 持续时长（小时）
seat_ids:         # 指定座位ID（null=随机选）
  - 12920
  - 12921

max-retry: 20          # 最多重试20次
```

### 5. 开启 Actions

进入 **Actions** 页签，点击 "I understand my workflows, go ahead and enable them"。

### 6. 完成 ✅

* 配置完成后，你可以在 **Actions** 页签中点击 **Run workflow** 手动触发预约。
* 定时触发已配置（北京时间 20:57），但**未成功测试**，不建议依赖。

> [!CAUTION]
> **关于定时触发的说明：**
>
> - GitHub Actions 的 `schedule` 触发机制**不稳定**，可能延迟或完全不触发
> - **不建议依赖定时功能进行抢座**，建议使用手动触发或本地运行
> - 如果一定要使用定时，请确保：
>   1. `config/booking_config.yml` 中 `begin` 时间设为预约开放时间
>   2. cron 时间设为比 `begin` 早 3-5 分钟
>   3. 保持仓库活跃（定期手动触发或推送代码）
>   4. 设置正确的 Workflow permissions（Read and write）

---

## 📋 自习室编号对照（详见zwu_lib.xlsx）

| 编号 | 自习室    | 座位数 | 座位ID范围  |
| :--: | --------- | :----: | ----------- |
|  0  | 自习室112 |  298  | 13344-13688 |
|  1  | 自习室113 |  316  | 13124-13799 |
|  2  | 自习室114 |  216  | 12806-13035 |
|  3  | 自习室212 |  242  | 12435-14862 |
|  4  | 自习室213 |  248  | 12186-12434 |
|  5  | 自习室214 |  242  | 11939-12183 |
|  6  | 自习室312 |  208  | 11720-11938 |
|  7  | 自习室313 |  224  | 11550-14848 |
|  8  | 自习室314 |  174  | 11376-11549 |

---

## 📦 配置参数说明

| 参数                   | 类型 | 默认值         | 说明                            |
| ---------------------- | :--: | -------------- | ------------------------------- |
| `room_id`            | int | 2              | 自习室编号（0-8）               |
| `dday`               | int | 2              | 延后天数（1=明天，2=后天）      |
| `begin`              | int | 21             | 开始时间（8=8:00，21=21:00）    |
| `duration`           | int | 9              | 持续时长（小时）                |
| `seat_ids`           | list | [12920, 12921] | 指定座位 ID，null 则随机选      |
| `cron-delta-minutes` | int | 5              | ⚠️ 已废弃，脚本启动后直接预约 |
| `max-retry`          | int | 20             | 最大重试次数                    |
| `notification_type`  | str | none           | 通知方式：none / wechat / email |
| `sckey`              | str | ''             | Server酱推送 Key                |

---

## 💬 通知配置

### Server酱微信通知（推荐）

1. 访问 [Server酱](https://sct.ftqq.com/)，扫码关注公众号
2. 获取 SCKEY
3. 两种方式任选其一：

> [!TIP]
> 有 SCKEY 时自动启用微信通知，无需手动设置 `notification_type`。

**方式一：GitHub Secrets（推荐，不会提交到仓库）**

进入仓库 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加：

| 环境变量名 | 说明             |
| ---------- | ---------------- |
| `SCKEY`  | Server酱推送 Key |

**方式二：配置文件**

编辑 `config/booking_config.yml`：

```yaml
notification_type: wechat
sckey: '你的SCKEY'
```

**通知消息格式：**

**预约成功：**

```
ZWU图书馆助手

- 日期: 2026-07-02（周四）
- 时间: 12:00 ~ 21:00
- 持续时长: 9h
- 自习室: 自习室114
- 座位号: 10
- 座位ID: 12920
```

**预约失败：**

```
ZWU图书馆助手

- 用户: 你的学号
- 状态: ❌ 预约失败
- 原因: 已有预约，请勿重复预约！
```

### 邮件通知（未测试）

```yaml
notification_type: email
smtp:
  server: smtp.office365.com
  from_addr: '你的邮箱'
  password: '你的密码'
  to_addr: '收件邮箱'
```

---

## 🖥️ 本地化部署

<details>
<summary>点击展开本地运行指南</summary>

### 环境要求

- Python 3.11+
- Google Chrome 浏览器
- chromedriver（需要与 Chrome 版本匹配）

### 安装

```bash
git clone https://github.com/yunshujing/ZWU_AUTO_BOOK_NEXT.git
cd ZWU_AUTO_BOOK_NEXT
pip install -r requirements.txt
```

### 配置

1. 复制账号配置样例并填入你的信息：

```bash
cp config/accounts_config.example.json config/accounts_config.json
```

2. 编辑 `config/accounts_config.json`，填入学号密码：

```json
[
    {"username": "你的学号", "password": "你的密码"}
]
```

3. 编辑 `config/booking_config.yml` 配置预约参数（自习室、时间等）。
4. 如果需要微信通知，配置 `sckey` 或在 GitHub Secrets 中添加。

### 运行

```bash
python demo.py
```

### 更新 ChromeDriver（可选）

本地运行需要 chromedriver 与 Chrome 版本匹配。如果遇到版本不匹配问题，可以运行：

```bash
python update_driver.py
```

此脚本会自动检测本机 Chrome 版本并下载匹配的 chromedriver。

</details>

---

## 📁 文件结构

```
ZWU_AUTO_BOOK_NEXT/
├── demo.py                          # 入口文件
├── zwulib.py                        # 核心库（登录/搜座/抢座）
├── notice.py                        # 通知模块（Server酱 + 邮件）
├── update_driver.py                 # ChromeDriver 自动更新工具（本地用）
├── _config.yml                      # API 配置
├── requirements.txt                 # Python 依赖
├── zwu_lib.xlsx                     # 座位信息映射表
├── config/
│   ├── accounts_config.json         # 多账号配置（含凭据，本地用）
│   ├── accounts_config.example.json # 账号配置样例
│   └── booking_config.yml           # 预约参数 + 通知配置
├── .github/
│   └── workflows/
│       └── main.yml                 # GitHub Actions 自动化
└── README.md                        # 本文件
```

---

## 🙏 致谢

- [浙江万里学院图书馆座位预约脚本](https://github.com/ZWUTA/ZWU_AUTO_Booking)
- [杭州电子科技大学图书馆预约](https://github.com/HaleyCH/HDU_AUTO_BOOK-public)

---

## 📄 开源协议

[MIT License](LICENSE)