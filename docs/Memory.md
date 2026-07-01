# Memory.md — 项目开发记录

> 本文件记录项目的关键决策、已完成工作和重要上下文，防止上下文溢出后丢失信息。
> 新的 agent 应通过 README.md、RULES.md 和本文件完全了解项目。

---

## 最近更新 (2026-06-30)

### 1. 时间窗口抢座 + 重试机制 ✅ 已完成

**需求**：借鉴参考项目，预约时间窗口内自动循环重试，提高抢座成功率。

**改动文件**：

| 文件 | 改动 |
|------|------|
| `config/booking_config.yml` | 新增 `cron-delta-minutes` 和 `max-retry` 配置 |
| `zwulib.py` | 新增时间窗口判断 + 重试循环 |
| `demo.py` | 传递新参数 |
| `.github/workflows/main.yml` | cron 改为 UTC 12:55（北京时间 20:55） |

**抢座策略**：
- cron-delta-minutes: 5（提前5分钟启动）
- max-retry: 20（最多重试20次）
- 重试间隔：窗口总秒数 / (max-retry - 2) - 10，至少5秒
- 时间窗口：预约开放前5分钟 ~ 开放后15分钟

**配置示例**（`config/booking_config.yml`）：
```yaml
begin: 21              # 晚上9点开放预约
cron-delta-minutes: 5  # 提前5分钟启动
max-retry: 20          # 最多重试20次
```

---

## 最近更新 (2026-06-29)

### 1. GitHub Actions 自动化部署 ✅ 已完成

**需求**：支持 GitHub Secrets 配置 + Server酱微信通知 + GitHub Actions 定时自动预约。

**参考项目**：`HDU_AUTO_BOOK-public-2`（杭电版），适配本校（浙万里）特殊配置。

**改动文件**：

| 文件 | 改动 |
|------|------|
| `.github/workflows/main.yml` | 新增 GitHub Actions 工作流 |
| `config/booking_config.yml` | 新增默认预约参数配置 |
| `config/accounts_config.json` | 新增多账号配置（本地运行用） |
| `config/accounts_sample.json` | 新增多账号配置示例 |
| `demo.py` | 重构：支持环境变量 + 本地配置 fallback |
| `zwulib.py` | 解耦通知逻辑：移除 `notice` 导入，返回结果由 demo 层处理 |
| `notice.py` | 重构：支持 Server酱（微信）和邮件两种通知方式 |
| `_config.yml` | 移除 SMTP 配置 |
| `requirements.txt` | 新增依赖清单 |
| `.gitignore` | 新增 `config/` 下的敏感文件 |

**配置方式**：

| 配置项 | 位置 | 说明 |
|--------|------|------|
| 账号凭据 | `config/accounts_config.json` | 提交到仓库（需 Private 仓库） |
| 预约参数 | `config/booking_config.yml` | 提交到仓库 |
| Server酱 Key | Secret: `SCKEY` | 唯一需要配的 Secret |

**教程推荐流程**：Fork → 设为 Private → 改 `config/` 下两个文件 → 配 SCKEY → 开启 Actions

**保留的本校特殊配置**：
- API 子域名：`zjwu.huitu.zhishulib.com`
- 自习室名称/ID 映射（9 个房间，2168 个座位）
- 登录页 XPath 选择器
- Referer 中的 openid
- space_category 参数（category_id=591, content_id=11）

**已修复的 Bug**：
- 时间窗口等待逻辑（之前打印等待但直接退出）
- 配置加载过滤（之前丢弃 cron-delta-minutes 等关键配置）
- 座位信息查询（返回 Series 而非标量）
- 空座位 DataFrame 处理
- seatid=None 时通知崩溃
- max_retry 边界值保护

**通知方式**：
- `wechat`：Server酱推送（推荐）
- `email`：邮件通知（保留）
- `none`：不通知

**保留的本校特殊配置**：
- API 子域名：`zjwu.huitu.zhishulib.com`
- 自习室名称/ID 映射（9 个房间，2168 个座位）
- 登录页 XPath 选择器
- Referer 中的 openid
- space_category 参数（category_id=591, content_id=11）

### 2. 多账号预约支持 ✅ 已完成

**需求**：两个账号（2023014380、2022014985）预约同一自习室的同一座位。

**改动文件**：

- `demo.py` — 增加 `MULTI_MODE` 开关、`ACCOUNTS` 列表、循环执行逻辑
- `zwulib.py` — `exit(-1)` 改为 `return`（防止单账号失败中断其他账号）
- `zwulib.py` — 座位预约间隔从 2 秒改为 5 秒（避免频率限制）
- `zwulib.py` — `notice()` 调用添加 `try/except`（防止通知异常中断预约）

**使用方式**：

```python
MULTI_MODE = True   # True=多账号依次执行，False=只执行第一个
MULTI_MODE = False  # 单账号模式
```

**关键经验**：

- 智数图平台有频率限制，座位间隔至少 5 秒
- `exit(-1)` 在多账号场景下必须改为 `return`，否则一个失败全部终止

### 2. 多账号配置优化 ✅ 已完成

**需求**：支持每个账号单独配置和多账号共享配置两种方案。

**改动文件**：

- `demo.py` — 重构为 `DEFAULTS` + `ACCOUNTS` 结构，支持账号级覆盖
- `readme.md` — 更新使用文档，新增三种配置方式说明
- `.gitignore` — 添加 `accounts_config.json` 防止凭据泄露

**配置方式**：

```python
# 方式一：所有账号共享默认参数
DEFAULTS = {
    'room_id': 2,
    'dday': 2,
    'begin': 12,
    'duration': 9,
    'seat_ids': [12920, 12921],
}

# 方式二：账号级覆盖（想改哪个加哪个）
ACCOUNTS = [
    {'username': '学号1', 'password': '密码1'},  # 用默认配置
    {'username': '学号2', 'password': '密码2', 'room_id': 4, 'seat_ids': None},  # 单独配置
]

# 方式三：外部 JSON 配置文件（accounts_config.json）
```

**关键设计**：

- 合并逻辑：`{**defaults, **per_account_overrides}`，账号级优先
- 移除 `MULTI_MODE` 开关（ACCOUNTS 列表长度即为模式）
- `zwulib.py` 无需修改（`appoint_zwulib()` 已支持所有独立参数）

### 3. 代码质量全面优化 ✅ 已完成

**需求**：修复代码审查发现的 15+ 个问题，提升代码质量和安全性。

**改动文件**：

- `demo.py` — 移除真实凭据（学号密码），替换为空占位符
- `zwulib.py` — 修复 7 个高优先级问题 + 清理低优先级问题
- `notice.py` — 邮箱配置外部化，修复日期/时间显示问题
- `_config.yml` — 新增 SMTP 配置，修复 Origin 头多余字符

**zwulib.py 主要改动**：

| 问题 | 修复 |
|------|------|
| Chrome 进程泄漏 | `appoint_zwulib` 添加 `try/finally` 确保 `driver.quit()` |
| 重试循环无 break | 遇到"重复预约"立即 `break`，避免发 12 封重复邮件 |
| 指定座位成功后继续 | `_book_specific_seats` 成功后立即 `return` |
| 请求体 `&&` 双符号 | 修正为单个 `&` |
| `begin` 参数未使用 | 从 `__init__` 签名中移除 |
| HTTP 请求无 timeout | 所有 `requests.post()` 添加 `timeout=30` |
| 通知日期显示数字 | 改为计算实际日期（`dday` → `YYYY-MM-DD`） |
| 死代码 `get_one_study_room_seat()` | 已删除 |
| 未使用变量 `time_zone` | 已删除 |
| 命名冲突 `self.pd`/`self.type` | 改为 `self.password`/`self.room_id` |
| 通配符导入 | 改为显式导入 `from notice import notice` |

**notice.py 主要改动**：

- 邮箱配置从硬编码改为读取 `_config.yml` 的 `smtp` 部分
- 日期显示：`dday` 数字 → 实际日期（如 `2026-07-01`）
- 移除硬编码时间范围（原 `8:00-21:00`）
- 添加座位信息查询错误处理
- 删除注释掉的测试代码

### 4. README 规范化 ✅ 已完成

- 基于 `zwu_lib.xlsx` 提取完整自习室数据（座位数、ID范围、座位号范围）
- 共 9 个自习室、2168 个座位

### 5. 邮件通知中文编码修复 ✅ 已完成

- `notice.py` 中 `formataddr()` 添加 `charset='utf-8'` 参数
- 修复 `UnicodeEncodeError: 'ascii' codec can't encode characters` 错误

### 6. GitHub PR 合并 ✅ 已完成

- `feat/project-rename` 分支已合并到 `main`
- 合并提交：`c2484a2`

---

## 项目架构要点

### 核心文件


| 文件               | 职责                                                      |
| -------------------- | ----------------------------------------------------------- |
| `zwulib.py`        | 核心库：`SeatAutoBooker` 类 + `appoint_zwulib()` 编排函数 |
| `demo.py`          | 入口：支持 GitHub Secrets 环境变量 + 本地配置 fallback |
| `notice.py`        | 通知模块：Server酱（微信）+ 邮件通知 |
| `update_driver.py` | ChromeDriver 自动更新（独立工具）                         |
| `_config.yml`      | API URL、请求头（模拟微信小程序）|
| `config/booking_config.yml` | 预约参数 + 通知配置（SCKEY、SMTP）|
| `config/accounts_config.json` | 多账号本地配置（含凭据，gitignore） |
| `config/accounts_sample.json` | 多账号配置示例 |
| `zwu_lib.xlsx`     | 座位 ID → 房间/座位号映射                                |
| `accounts_config.json` | 多账号本地配置文件（可选，含凭据）               |
| `.github/workflows/main.yml` | GitHub Actions 定时自动预约 |

### 预约流程

1. Selenium headless Chrome 登录 → 提取 cookies
2. POST API 获取用户 UID
3. 搜索可用座位 或 指定座位 ID 预约
4. 失败重试最多 12 次（间隔 30 秒）
5. 邮件通知（已用 try/except 包裹，失败不中断）

### 学校特有内容（适配其他学校需修改）

- API 子域名：`zjwu.huitu.zhishulib.com`
- 自习室名称列表（9个房间）
- 座位 ID 范围（见下方自习室表格）
- 座位映射表（`zwu_lib.xlsx`）
- 登录页 XPath 选择器
- Referer 中的 openid
- space_category 参数（category_id=591, content_id=11）

### 9 个自习室（从 zwu_lib.xlsx 提取）


| 编号 | 自习室 | 座位数 | 座位ID范围  |
| ------ | -------- | -------- | ------------- |
| 0    | 112    | 298    | 13344-13688 |
| 1    | 113    | 316    | 13124-13799 |
| 2    | 114    | 216    | 12806-13035 |
| 3    | 212    | 242    | 12435-14862 |
| 4    | 213    | 248    | 12186-12434 |
| 5    | 214    | 242    | 11939-12183 |
| 6    | 312    | 208    | 11720-11938 |
| 7    | 313    | 224    | 11550-14848 |
| 8    | 314    | 174    | 11376-11549 |

---

## Git 提交历史

```
2a12c27 feat: GitHub Actions 自动化 + Server酱通知 + 时间窗口抢座
64f6686 docs: 补全Memory.md - _config.yml职责更新、Git历史同步
7605fc3 docs: 修复Memory.md - 章节编号重复、floorData引用过期
```

---

## 待办 / 已知问题

- [x]  邮件通知的 SMTP 配置已迁移到 `_config.yml`（需用户填写）
- [ ]  网络连接 GitHub 不稳定，推送经常失败（需要代理或 VPN）
- [x]  `get_one_study_room_seat()` 死代码已删除
- [x]  `time_zone = 8` 未使用变量已删除
