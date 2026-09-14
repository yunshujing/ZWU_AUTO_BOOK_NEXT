# ZWU_AUTO_BOOK_NEXT 架构与代码认知文档

> 面向后续开发与维护的项目基础认知文档。
> 目标：任何新接手的开发者读完本文即可定位改动点、理解调用链、明确约定与风险。
> 生成时间：2026-09-14

---

## 1. 项目定位

**浙江万里学院图书馆自习室座位自动预约脚本**。

- 目标站点：`https://zjwu.huitu.zhishulib.com/`（智数图 / 汇图 Seat 系统）
- 核心动作：自动登录 → 查询可用座位 → 在预约开放时间窗口内反复抢座 → 推送结果通知
- 运行形态：**单进程脚本**，无服务端、无数据库、无 Web 层。两种部署方式：
  1. **GitHub Actions**（主推）：由外部 cron-job.org 定时触发 workflow（因 GitHub `schedule` 不准时）
  2. **本地直接运行**：`python demo.py`
- 多账号：一个进程内**串行**遍历账号列表，逐个预约

---

## 2. 技术栈

| 分类 | 选型 | 用途 |
|---|---|---|
| 语言 | Python 3.11+（CI 用 3.12） | 全项目 |
| HTTP | `requests` | 搜索座位 / 提交预约 / 飞书 API / Server酱 |
| 浏览器自动化 | `selenium` + `webdriver-manager` | **仅用于登录**并提取 Cookie |
| 配置解析 | `pyyaml` | `_config.yml`、`booking_config.yml` |
| 表格处理 | `pandas` + `openpyxl` | 读取 `zwu_lib.xlsx` 座位映射表 |
| 通知 | `requests`（Server酱）、`smtplib`（邮件，未实测） | 结果推送 |
| Windows 专用 | `winreg`、`zipfile`、`shutil`、`subprocess` | `update_driver.py` 自动更新 ChromeDriver |
| CI | GitHub Actions（ubuntu-latest） | 定时/手动触发 |

**依赖清单**（`requirements.txt`）：`requests>=2.28.0`、`pyyaml>=6.0`、`pandas>=1.5.0`、`openpyxl>=3.0.0`、`selenium>=4.0.0`、`webdriver-manager>=4.0.0`。
无 lint / test runner / packaging 依赖 —— 测试是**零依赖的纯 `print`+`assert` 脚本**。

**关键技术特征**：登录用浏览器自动化（拿不到纯 API 登录方案），抢座用**纯 HTTP 请求**（性能优先）。

---

## 3. 目录结构

```
ZWU_AUTO_BOOK_NEXT/
├── demo.py                          # ★ 唯一入口：配置加载 + 账号循环 + 编排
├── zwulib.py                        # ★ 核心预约引擎（SeatAutoBooker + appoint_zwulib）
├── seatmap.py                       # 座位号 → 座位ID 映射转换 + CLI 查询工具
├── notice.py                        # 通知模块（Server酱 / 邮件）
├── update_driver.py                 # ChromeDriver 自动更新（仅 Windows 本地）
├── test_config_layering.py          # 配置分层与加载优先级测试
├── test_seatmap.py                  # 座位号转换、飞书解析、主循环接线测试
├── _config.yml                      # ★ 平台 API 契约：target URL / start-time / 请求头
├── requirements.txt
├── zwu_lib.xlsx                     # ★ 座位映射数据源（room / id / title）
├── LICENSE                          # MIT
├── config/
│   ├── booking_config.yml           # 默认预约参数 + 通知配置
│   ├── accounts_config.json         # 本地账号配置（含密码，gitignore，当前不存在）
│   ├── accounts_config.example.json # 本地配置样例（含 password）
│   └── accounts.example.json        # GitHub Variable 样例（不含 password）
├── docs/
│   ├── RULES.md                     # 12 条开发规则 + 5 条黄金律
│   └── ARCHITECTURE.md              # 本文档
├── .github/workflows/main.yml       # CI：手动触发 + 环境变量注入 → python demo.py
└── image/readme/*.png               # README 配图
```

---

## 4. 模块职责与调用链

### 4.1 `demo.py` — 入口 / 编排层（唯一的 `__main__`）

职责：**配置加载 + 参数合并 + 循环调度 + 通知分发**。不含任何抢座逻辑。

关键函数：

| 函数 | 职责 |
|---|---|
| `load_accounts()` | 4 条加载路径按优先级短路返回（见 §6） |
| `_parse_passwords_secret()` | 解析 `PASSWORDS` Secret（`{学号: 密码}`） |
| `_merge_passwords()` | 用 `username` 关联密码表/Secret，**共用**于分层与飞书两条路径 |
| `_load_accounts_split()` | `ACCOUNTS_CONFIG`(明文) + `PASSWORDS`(密文) 合并 |
| `_load_accounts_from_feishu()` | 飞书多维表格：取 token → 分页读表 → 字段解析 → 合并密码 |
| `_feishu_get_records()` | 通用分页读取器（账号表 / 密码表复用） |
| `_text_field_value()` / `_parse_int_list_field()` | 飞书字段兼容层（字符串 或 `[{"text":...}]`） |
| `resolve_final_seats()` | ★ 座位决策：账号级 > 默认层；`seat_ids` > `seats`（座位号需查表） |
| `_seats_to_ids()` | 调用 `seatmap.resolve_seats()` 并打印转换日志 |
| `load_booking_config()` | 读 `booking_config.yml` → 合并 `SCKEY` 环境变量 → 有 SCKEY 自动启用微信通知 |

主循环（`__main__`）逻辑：
1. `load_accounts()` → 空则 `exit(1)`
2. `load_booking_config()` 取默认值
3. 逐账号：校验 `username`/`password` → 检查 `enabled` → **参数合并** `params = {**defaults, **account}`（剔除 `username`/`password`/`enabled`）→ 覆盖 `seat_ids` → 调 `appoint_zwulib()`
4. 按 `stat` 分发 `notify()` / `notify_fail()`，通知异常被 `try/except` 吞掉并打印，**不中断循环**

### 4.2 `zwulib.py` — 核心预约引擎

- `ROOM_NAMES`：9 个自习室名（索引即 `room_id` 0-8），`room(room_id)` 做带边界校验的查名。
- `class SeatAutoBooker`：单个账号的会话对象。
  - `__init__`：初始化 headless Chrome（`--headless --no-sandbox --disable-dev-shm-usage --disable-gpu --disable-extensions`）；**优先系统 `chromedriver`**（`shutil.which`），否则 `webdriver-manager` 自动下载；加载 `_config.yml`（`utf-8-sig`）得到 `start_time` / `book_url` / `headers`。
  - `_calc_total_seconds(dday, start_hour)`：★ 时间换算核心。当前北京时间零点 + `dday` 天 + `start_hour` 小时 − `start-time`(1970-01-01 08:00:00 北京时间)，得到 API 所需的秒偏移。
  - `login()`：Selenium 打开首页 → 按 `name="login_name"` 填学号 → 按**硬编码 XPath** 填密码 → 点登录 → `sleep(8)` → 拼接 Cookie 字符串写入 `self.headers['Cookie']` → URL 仍含 `login` 判为失败（返回 −1）。
  - `get_user_info()`：POST `searchSeats?LAB_JSON=1` 取响应 `DATA`，提取 `uid`（后续下单必填）。
  - `book_favorite_seat(dday, start_hour, duration, cron_delta_minutes, max_retry)`：重试主循环，`retry_interval = 60s`，`max_retry` 下限强制为 3；`stat=="ok"` 或消息含"请勿重复预约"→ **立即返回**。
  - `_book_specific_seats()`：按 `seat_ids` **顺序逐个**提交，成功即返回；每个之间 `sleep(5)` 防频率限制。
  - `_book_random_seat()`：POST `searchSeats` 拉全量座位 → `pandas` 建表 → 过滤 `room == 当前室 & state(ava)==0 & title % 2 == 0`（**偶数座位号，假设有插座**）→ `random.choice` → 提交。
- `appoint_zwulib(...)`：对外唯一入口函数。创建会话 → `login` → `get_user_info` → `book_favorite_seat` → `finally: driver.quit()`；统一返回 `(stat, msg, seatid)` 三元组。

### 4.3 `seatmap.py` — 座位号 ↔ 座位ID 映射

- 数据源 `zwu_lib.xlsx`（列：索引 / `room` / `id` / `title`）。
  - `id` = 平台座位ID（`seat_ids` 配置项）
  - `title` = 座位号（选座页显示的人读编号，从 1 开始）
- `load_seat_map()`：进程内 `_seat_map_cache` 缓存，返回 `{room_id: {座位号: 座位ID}}`。
- `resolve_seats(room_id, seat_numbers)`：座位号 → 座位ID；非数字或不存在 → 打印警告并跳过；全无效 → 返回 `[]`（调用方视为随机选座）。
- `main()`：CLI 工具（打印全部映射 / 查询指定座位号）。
- 反向依赖：从 `zwulib` 引入 `ROOM_NAMES`、`room`。

### 4.4 `notice.py` — 通知层

- `notify(user, dday, seatid, config)`：按 `notification_type` 分发 `_send_wechat` / `_send_email`，否则跳过。
- `notify_fail(user, reason, config)`：**仅微信通道**，未启用微信或缺失 `sckey` 时静默返回。
- `get_seat_info(seatid)`：改为从 `seatmap` 导入，复用其进程内缓存反查 `room/title`（旧实现在每次通知时重新 `read_excel`），异常时降级为 `未知`。
- 消息体：日期（北京时间 + `dday` 天）+ 中文星期 + 时间段（`begin ~ begin+duration`）+ 自习室 + 座位号 + 座位ID。
- Server酱端点：`https://sctapi.ftqq.com/{sckey}.send`。

### 4.5 `update_driver.py` — 本地运维工具

独立的 Windows 工具脚本（**不被任何模块 import**）：注册表 / 文件属性探测 Chrome 版本 → 从 Chrome for Testing `known-good-versions` API 找主版本匹配的 win64 驱动 → 下载 / 解压 / 备份 / 替换到 `drivers/chromedriver.exe`。

### 4.6 `_config.yml` — 平台 API 契约（勿随意改动）

| 键 | 值 | 说明 |
|---|---|---|
| `target` | `.../Seat/Index/bookSeats?LAB_JSON=1` | 下单接口 |
| `start-time` | `1970-01-01 08:00:00` | 秒偏移计算基准（北京时间，对应 UTC 零点） |
| `headers` | Host / UA(伪装微信小程序) / `X-Requested-With: com.tencent.mm` / Origin / Referer 等 | 请求头模板，Cookie 运行时注入 |

---

## 5. 整体数据流

```
                    ┌─────────────── 触发 ───────────────┐
   cron-job.org ──▶ GitHub Actions(main.yml) ──▶ python demo.py
   本地手动 ─────────────────────────────────────▶ python demo.py
                    └────────────────────────────────────┘
                                   │
                          ┌────────▼────────┐
                          │  demo.load_accounts()  4 条路径优先级短路 │
                          │  ① 本地 json ② ACCOUNTS ③ ACCOUNTS_CONFIG+PASSWORDS ④ 飞书表格 │
                          └────────┬────────┘
                                   │  [{username, password, ...覆盖字段}]
                          ┌────────▼────────┐
                          │ load_booking_config() │  booking_config.yml + SCKEY
                          └────────┬────────┘
                                   │  params = {**defaults, **account}
                          ┌────────▼────────┐
                          │ resolve_final_seats() │  seats ──seatmap──▶ seat_ids
                          └────────┬────────┘
                                   │
            ╔══════════════════════▼══════════════════════╗
            ║  逐个账号：appoint_zwulib()  (zwulib.py)     ║
            ║   Selenium ──▶ 登录 ──▶ Cookie / uid         ║
            ║   requests ──▶ searchSeats (查空位)          ║
            ║   requests ──▶ bookSeats  (重试 ≤ max_retry) ║
            ╚══════════════════════╤══════════════════════╝
                                   │  (stat, msg, seatid)
                          ┌────────▼────────┐
                          │ notice.notify / notify_fail │  Server酱 / SMTP
                          └─────────────────┘
```

---

## 6. 配置体系（本项目最复杂的部分）

### 6.1 账号加载优先级（短路，命中即返回）

| 序 | 来源 | 密码位置 | 形态 |
|---|---|---|---|
| 1 | `config/accounts_config.json` | 同文件内 | 本地运行主方式 |
| 2 | `ACCOUNTS` Secret | 同 JSON 内 | 老用法，后向兼容（会打印迁移提示） |
| 3 | `ACCOUNTS_CONFIG` Variable + `PASSWORDS` Secret | 分离 | 推荐方式：明文清单 + 密文映射 |
| 4 | 飞书多维表格（4 个 `FEISHU_*` Secret） | 密码表 或 `PASSWORDS` | 最便捷，手机可改；**密码表优先，Secret 兜底** |

### 6.2 参数合并层级（可用字段：`room_id` / `dday` / `begin` / `duration` / `seat_ids` / `seats` / `max-retry` / `enabled`）

```
账号级显式配置  >  booking_config.yml 默认值  >  demo.DEFAULTS 内联兜底
```

座位解析另有独立规则：`账号级(seat_ids>seats)` > `默认层(seat_ids>seats)` > `None(随机选座)`。
同层 `seat_ids` 与 `seats` 同时存在 → **以 `seat_ids` 为准并打印警告**。

### 6.3 环境变量总表

| 变量 | 来源 | 必需性 |
|---|---|---|
| `ACCOUNTS` | Secret | 路径② |
| `ACCOUNTS_CONFIG` | **Variable** | 路径③ |
| `PASSWORDS` | Secret | 路径③/④ 兜底 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_APP_TOKEN` / `FEISHU_TABLE_ID` | Secret | 路径④ |
| `FEISHU_PASSWORD_TABLE_ID` | Secret | 路径④ 可选 |
| `SCKEY` | Secret | 可选，**覆盖** yml 中的 `sckey` |

---

## 7. 关键业务逻辑要点

1. **时间换算**：所有时间基于北京时间（`timezone(timedelta(hours=8))`）计算，不依赖系统时区。`total_seconds` 是相对 `start-time` 的偏移，直接作为 API 参数。
2. **登录与抢座分离**：Selenium 只负责登录拿 Cookie；抢座走纯 HTTP，避免浏览器开销，支撑分钟级高频重试。
3. **重试策略**：`max_retry` 次 × 每次约 60s 间隔；命中"成功"或"请勿重复预约"提前退出。异常不抛出，也走 `sleep(60)` 继续。
4. **选座两分支**：指定座位（顺序试、失败换下一个）vs 随机座位（偶数编号 + 空闲）。
5. **容错设计**：通知失败被隔离（`try/except` 打印，不影响主流程）；单账号失败不阻断后续账号；密码缺失的账号**跳过而非报错退出**。
6. **`enabled` 开关**：在**主循环统一**判断，四条加载路径都不做该判断（保持单一职责）。
7. **`cron-delta-minutes` 已废弃**：仍被传入 `appoint_zwulib` 和重试循环，但当前实现是**启动即预约**，该参数不再产生等待行为（README 已标注）。

---

## 8. 构建与运行方式

```bash
# 安装
pip install -r requirements.txt

# 本地配置
cp config/accounts_config.example.json config/accounts_config.json   # 填学号密码
# 编辑 config/booking_config.yml（自习室/时段/座位/通知）

# 运行
python demo.py

# 座位号查询
python seatmap.py 2 113      # 自习室114的113号 → 座位ID
python seatmap.py 2          # 打印自习室114全部映射

# 测试（零依赖，不触发真实预约，内部 mock appoint_zwulib）
python test_config_layering.py
python test_seatmap.py

# 本地驱动维护（仅 Windows）
python update_driver.py
```

**CI**（`.github/workflows/main.yml`）：`workflow_dispatch` 手动触发；`schedule` **已注释**（改由 cron-job.org 精确触发，时延约 1-2 秒）；Python 3.12 + pip 缓存；一步注入全部环境变量后 `python demo.py`。CI 环境依赖 runner 预装的 Chrome/chromedriver（`shutil.which('chromedriver')` 命中）。

---

## 9. 代码组织方式总结

- **分层清晰、无框架**：`入口编排(demo) / 核心引擎(zwulib) / 数据映射(seatmap) / 通知(notice)` 四层单向依赖，无循环引用。
- **依赖方向**：`demo → {zwulib, notice, seatmap}`；`seatmap → zwulib`（复用 `ROOM_NAMES`）；`notice`、`update_driver` 完全独立。
- **契约外置**：API 地址与请求头放 `_config.yml`，参数默认值放 `booking_config.yml`，座位数据放 `zwu_lib.xlsx`——代码内不硬编码业务数据。
- **配置兼容优先**：`load_accounts()` 用**优先级短路**同时支持 4 套历史/新方案，老用法保留并主动提示迁移。
- **返回值统一**：预约链路统一 `(stat, msg, seatid)` 三元组，便于通知层消费。
- **测试策略**：不引入 pytest，直接可执行脚本 + `redirect_stdout` 捕获 + monkey-patch `appoint_zwulib`，保证测试不产生真实预约副作用。
- **风格约定**（`docs/RULES.md`）：小改动优先、匹配现有风格、失败要显式暴露、每功能点 `git commit`（Conventional Commits）、同步更新 README。
- **命名约定**：内部辅助函数带 `_` 前缀；配置键混用下划线（`room_id`）与连字符（`max-retry`、`cron-delta-minutes`），后者沿用上游项目习惯。

---

## 10. 已知风险与维护注意点

> 状态截至 2026-09-14（性能优化批次 A/B 已完成，详见 `docs/OPTIMIZATION_PLAN.md`）。

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| 1 | `zwulib.py::login` | 密码框/登录按钮用**硬编码绝对 XPath** | ⚠️ 未解决：目标站前端一改版即登录失败 |
| 2 | `zwulib.py::_book_random_seat` | 依赖响应深层路径 `allContent.children[2].children.children` | ⚠️ 未解决：接口结构调整即崩溃 |
| 3 | `zwulib.py::book_favorite_seat` | `cron_delta_minutes` 已废弃但仍在签名与调用中 | ⚠️ 已标注废弃，尚未移除（保兼容） |
| 4 | `_config.yml` 请求头含固定 `Referer`（带 openid） | 若服务端校验 Referer，可能失效 | ⚠️ 未解决 |
| 5 | 通知模块 | 邮件通道标注"未测试"；失败通知仅支持微信 | ⚠️ 功能覆盖面限制 |
| 6 | 凭证安全 | `config/accounts_config.json` 含明文密码（需保持 gitignore） | ⚠️ 误提交即泄露 |
| 7 | 使用协议 | README 明确禁止"预约不签到"等占用公共资源行为 | ⚠️ 滥用可能导致封号/项目下架 |
| 8 | ~~`demo.py` 主循环未捕获异常~~ | 单账号 Chrome 构造失败会**中断整批账号** | ✅ 已修复（`process_account` 隔离） |
| 9 | ~~`update_driver.py` 写入不存在的 `drivers/`~~ | 首次运行 `shutil.copy2` 抛异常 | ✅ 已修复（`os.makedirs`） |
| 10 | ~~`notice.py::get_seat_info` 重复 `read_excel`~~ | 与 `seatmap` 重复实现同一份数据源 | ✅ 已修复（迁至 `seatmap` 并复用缓存） |
| 11 | ~~三处默认值不一致~~ | `room_id` 曾为 `2/2/3` | ✅ 已修复（统一为 `2/2/2`） |
| 12 | ~~固定 60s × 20 次重试~~ | 单账号最坏阻塞 20 分钟并堵塞后续账号 | ✅ 已修复（探路→猛攻，最坏 273s） |
| 13 | ~~测试脚本复制主循环逻辑~~ | 改 `demo.py` 后测试仍通过（假绿） | ✅ 已修复（改为调用 `demo.process_account`） |

---

## 11. 后续开发建议

**已完成（2026-09-14）**

- ✅ `notice.get_seat_info` 改为走 `seatmap` 的进程内缓存（含 `id → (room, title)` 反查）
- ✅ 统一 `appoint_zwulib` 与 `demo.DEFAULTS` 的默认值
- ✅ `update_driver.py` 增加 `os.makedirs(drivers_dir, exist_ok=True)`
- ✅ 主循环抽为 `process_account()`，单账号异常不再中断整批
- ✅ 新增分阶段耗时埋点（`_stage` / `_log_timer`）
- ✅ 重试节奏改为「探路 → 猛攻」，并抽出 `_retry_intervals()` 独立可测

**待办**

1. **B 方案（提前登录 + 到点只抢）** —— 把登录前置到开抢之前，是进一步提升的关键；同时它也是"先全员过一轮再补抢"的前提（不前置登录，第一轮本身就会耗尽黄金时间）。需先验证凭证有效期。
2. **抽离登录选择器**到 `_config.yml` 或独立常量表，降低站点改版脆弱性（风险 #1）。
3. **清理废弃参数** `cron-delta-minutes`（README、yml、demo、zwulib 同步）。
4. **将三个测试脚本纳入 CI**（当前 workflow 只跑 `demo.py`，回归无防护）。
5. 为 `_book_random_seat` 的深层响应路径加防御性校验，避免站点调整即崩溃。
