# Job Hunter 代码检查报告与优化方案

**检查日期**：2026-08-16
**检查范围**：`boss_apply.py`、`shared.py`、`deep_filter.py`、`report.py`、`follow_up.py`、`hr_auto_reply.py`、`ab_test_track.py`、`failure_tracking.py`、`run_daily.sh`、`config.json`
**验证方式**：代码走查 + 隔离执行复现 + 日志体量实测

---

## 2026-08-26 凌晨批次实施记录

**已完成（含此前未记档批次）：**

| 项目 | 内容 | 落点 commit |
|---|---|---|
| match_engine 统一评分引擎 | 五维加权+可解释输出（新增能力），词边界修复误报，README 评估框架自动化 | `47a6b28` |
| B4 报告模板加固 | string.Template+分段拼装替代精确匹配字符串替换，动态字段统一 html.escape | `14f8e57` |
| A6 SIGINT 不写暂停锁 | Ctrl+C 只置 SHOULD_STOP 优雅退出本轮；SIGHUP/SIGTERM 保持写锁防 launchd 重拉 | `5326841` |
| hr_auto_reply 内联扫描 | 归档 boss_full.scan_messages 行为并入 _scan_chat_page(unread_only)，消除 ModuleNotFoundError 路径 | `70ba106` |
| A4 词表统一 | job_pools/city_pools 以 config.json 为唯一事实源，旧硬编码降级 LEGACY 兜底 | `7f0a3bd` |
| config.example.json 补全 | 补齐 salary_filter/body_exclude/job_pools/city_pools/safety/keyword_tiers/schedule 真实结构 | `b970489` |
| B5/B6 死代码清理 + CITY_CODES 统一 | 删 MAX_RETRIES/get_today_new/_fetch_company_jobs；CITY_CODES 收敛进 shared.py | `98379ef` |
| **R1（原 P3）合并两次全卡片 JS 遍历** | 查公司名+薪资合并为一次 run_js 返回 {company,salary}，查不到两字段空串、兜底/跳过分支语义不变 | `d2b2ce2` |
| **R2（原 P4）同页续投** | 每份投递后不再整页 reload：新增 `_reset_after_apply` 最小 DOM 重置残留态（移除残留聊天输入框/沟通按钮、清空详情面板，只删节点不点按钮）；仅 tab 被导航离开搜索页时才回退整页加载；翻页仍走懒加载滚动、换关键词走入口自愈 | `4cc2f94` |

**更正（从「仍待做」划掉）：**

- **B2（--daily 接线）已修好**（经核实为后续批次完成）：`boss_apply._resolve_cities/_resolve_keywords` 已从 `city_pools.city_priority` / `job_pools.keywords` 派生（顶层键优先），不再是"只跑深圳+智驾测试"。
- **tab 断连自愈已随 P0 批次提交**：`_looks_disconnected` + `_recover_search_tab`（`8287bd6`），投递循环异常路径已接线。
- **P1（全量 JSON 重写+fsync）已自然消解**：运行时只写 SQLite 单一事实源，`shared.save_log` 已无调用方；`*-log.json` 为迁移前只读历史快照。

**仍待做（如实盘点）：**

- **A8 部分**：异常 traceback 已入 events 表（event_error），但投递循环仍是巨型 try，未按步骤拆分。
- **A3 三副本未统一**：`~/.hermes/skills/job-hunting/job-hunter` 与 `~/.claude/skills/job-hunter` 副本仍在，与本仓库并存。
- 产品级方向（手动确认制投递 / kill switch 语义分层 / 三线简历实验重启）未启动。

---

## 2026-08-20 P0 实施记录（本仓库）

以下 P0 项已落地并有测试覆盖（`tests/test_p0.py` 8 项 + `tests/regression.py` 19 例）：

1. **B1 已修**：`shared.smart_filter` 补 `desc_lower`、`reason_parts` 上移、最终返回拼接原因。高薪实习（正文日薪≥300）实测保底 60 分并带"高薪实习"原因。
2. **B3 已修**：`shared.recent_activity` 改为按 `entry.time[:10]` 聚合，不再按文件 mtime 归日；SQLite 版 `store.recent_activity_days` 同规则。
3. **A1 单一事实源已建**：新增 `store.py`，投递/跳过/失败/事件全部写入 `ab_experiment.db` 新表 `jobs` / `applications_v2` / `events`。旧 `*-log.json` 保留为只读历史，启动自动迁移（或 `python3 store.py --migrate` 手动执行，幂等）。当前已迁移 40,070 条，与 JSON 口径一致（applied 6,139 / skipped 33,918 / failed 13）。
4. **A7 投递验证已加**：点击"立即沟通"后先验证会话打开（聊天输入框/聊天面板/已沟通态），再填发招呼语并验证输入框清空。结果状态机：`APPLIED`（已验证）/ `UNCERTAIN`（会话已开但发送未验证，需人工复核）/ `FAILED`（弹窗拦截/会话未打开）。招呼语失败不再静默。
5. **熔断改查库**：日/时熔断不再全量扫 JSON，改用 `store.count_applied_since()`（applied+uncertain 计入），顺带修掉 P2 的重复全量解析。
6. **报告/跟进/A-B/失败追踪全部改读 store**：`report.py` 增加"未验证投递"行；`follow_up.py`、`ab_test_track.py`、`failure_tracking.py` 均以 `applications_v2` 为准，旧 `applications` 表保留为历史视图。
7. **回归测试迁回仓库**：`tests/regression.py` + `tests/regression_cases.json`（19 例）+ `tests/test_p0.py`（8 项）。Hermes 副本里的旧测试不再需要。
8. **`followups.json` 已加 .gitignore**；`boss_apply.py` 清掉死代码（`re`/`date`/`timedelta` 未用导入）。

**已知迁移语义**：旧 JSON 中 245 条缺 `time` 的记录按"文件 mtime 当天中午 + 文件内序号"生成稳定 ID 导入，不伪造到今天；同文件内完全重复的条目会按 ID 幂等去重。

**仍待做（P1/P2）**：`--daily` 接线和 tab 断连自愈仍在工作区未提交；report 模板仍是字符串替换（B4）；投递后整页 reload（P4）与 company/salary 两次 JS（P3）未合并；SIGINT 仍写暂停锁（A6）；`hr_auto_reply.py` 默认模式 import 已归档的 `boss_full`（会 ModuleNotFoundError）；`ab_test_track.POOL_KEYWORDS` 仍与 config 词表不一致（A4）；`config.example.json` 缺 job_pools/city_pools/safety。
> **（2026-08-26 更正：上行为 08-20 时点快照，已全部过时——各项状态见上方「2026-08-26 凌晨批次实施记录」。）**

---

## 一、已确认的 Bug（影响正确性，建议优先修复）

### B1. `smart_filter` 高薪实习放行逻辑完全失效 — shared.py L225-242

- **现象**：`desc_lower` 在 L233/236/237 被引用，但函数内从未定义（只定义了 `combined`）。触发时抛 `NameError`，被 L238 的 `except Exception: pass` 静默吞掉 → `daily_salary` 恒为 0 → L240 `daily_salary >= 300` 永远为假，**"高薪实习放行"（score 保底 60）是死代码**。
- **潜在二次炸点**：L242 `reason_parts.append(...)` 在 L261 `reason_parts = []` 之前使用。若修复了 `desc_lower`，此处立刻 `NameError`。
- **复现证据**（隔离执行实测）：
  - 输入：标题"AI应用工程师实习"，正文"实习560-600元/天，转正后12-16K"，薪资"10-15K"
  - 期望：按设计意图放行并保底 60 分
  - 实际：返回 `40`（未放行），且无任何原因说明
- **修复**：函数开头补 `desc_lower = (desc or "").lower()`；把 `reason_parts = []` 上移到函数开头。

### B2. `--daily` 模式读取的配置键不存在 → 日常模式实际只跑"深圳+智驾测试"

- **现象**：`config.json` 的真实配置是 `job_pools.keywords`（5 组 64 词）和 `city_pools.city_priority`，但 `boss_apply.py` L930/L940 的 `--daily` 分支读的是 `target_cities` / `search_keywords` —— 这两个键在 config.json 中**不存在**，全部落回默认值 `["深圳"]` + `["智驾测试"]`。
- **后果**：`run_daily.sh` 每天只会投 1 城 1 词。8/15 那次"61 关键词 × 9 城市"是 Hermes 手动展开参数传的，脚本本身从未接线。
- **修复**：`--daily` 模式改为从 `job_pools.keywords` 展平关键词、从 `city_pools.city_priority` 展平城市（按 primary→secondary→opportunistic 排序），并支持 `--pool S级-AI应用工程师` 等选择指定组。

### B3. 报告近 7 天趋势按文件 mtime 统计，数据失真 — shared.py `recent_activity` L357-375

- **现象**：`recent_activity` 用 `f.stat().st_mtime` 归日。城市日志是"全历史单文件"，mtime = 最后一次写入时间。
- **后果**：深圳 log 现有 applied 1026 + skipped 5099，只要今天动过，趋势图就会把**全部 6125 条**记到今天。趋势图长期是假数据。
- **修复**：改按 entry 的 `time` 字段（`t[:10]`）聚合，mtime 只做文件级粗筛。

### B4. `report.py` 模板渲染极度脆弱且无 HTML 转义

- **现象**：`HTML_TEMPLATE` 写的是 Jinja2 语法，但实现是逐段字符串替换（L191/221/237 靠**精确空白匹配**整块替换）。模板缩进/换行一改，替换静默失败 → 表格整块消失。
- **隐患**：公司名/岗位名/薪资直接拼进 HTML，含 `<` `&` `"` 时页面损坏。
- **修复**：改真 Jinja2（或 `string.Template`）+ `html.escape()` 全部动态字段。

### B5. `MAX_RETRIES` 定义了从未使用 — boss_apply.py L1021

- 注释宣称"断连最多重试 2 次"，但全文无任何重试逻辑（grep 仅 1 处命中即定义处）。要么实现（建议只对搜索页加载失败重试，投递动作不重试防重复投），要么删除。

### B6. 其他小问题

| 位置 | 问题 |
|---|---|
| shared.py L337 `get_today_new` | 函数名"今天"，实现返回全部历史；全项目无调用（dead code），删除或改正 |
| boss_apply.py L1084 `hour_now` | 计算后未使用 |
| deep_filter.py L188 `_fetch_company_jobs` | `raise NotImplementedError`，实际执行器在 boss_apply 内联 JS —— 死代码，删或合并 |
| deep_filter.py L166 `CITY_CODES` 与 boss_apply.py L341 `CITY_CODES` | 两份重复表，已开始漂移（deep_filter 只有 12 城）→ 统一放 shared.py |

---

## 二、性能问题（日志规模上来后已成瓶颈）

实测：全部日志 **11MB**（40+ 个文件），深圳单文件 1.7MB、6127 条。

### P1. 全量 JSON 重写 + fsync — shared.py `save_log` L105-111

每次投递/跳过都 `write_text(indent=2)` 重写整城历史 + `os.fsync`。投 50 份 = 50+ 次重写 1.7MB 文件 + 刷盘。这是 **O(n²) 磁盘 IO**，也是主流程卡顿和日志损坏风险源（写一半被杀 → 整个城市日志 JSON 损坏）。

### P2. 跨进程熔断扫描 = 每关键词 2 次全量解析 11MB — boss_apply.py L1067-1094

每处理完一个关键词，日熔断（L1069-1075）和时熔断（L1087-1094）**各扫一遍全部 `boss-*-log.json`**，逐条读 `time` 字段。549 格（61 词×9 城）× 2 次 ≈ 每次运行解析 ~12GB 文本。是投递轮转间隙最大的纯 CPU/IO 开销。

### P3. 每份投递 2 次全卡片 DOM 遍历 — boss_apply.py L555-581

查公司名和查薪资是**两个几乎相同的 JS 块**，各自遍历所有 `.job-card-wrap` 按标题匹配。合并为一次 `run_js` 返回 `{company, salary}`。

### P4. 每投一份就整页 reload 搜索页 — boss_apply.py L797

`search_tab.get(search_url)` 重开页面 → 丢滚动位置、重新拉取渲染卡片，每份多花 3-6 秒且增加请求频率（对风控反而是负面信号）。应改为：同页 DOM 继续处理下一条；只有翻页/换词时才 reload。

---

## 三、架构与工程问题

### A1. 数据双轨 + 口径不一致

同一事实（一次投递）写三处：城市 JSON 日志、`ab_experiment.db`（靠 `--import-logs` 幂等导入）、`failure_reasons` 表。README 里累计投递就出现 5,927 / 5,968 / 5,274 三个口径。**建议**：JSON 日志改 SQLite（已有 ab_experiment.db 的 schema 基础）或 JSONL 追加式，单一事实源，删除导入逻辑。

### A2. 个人数据已提交进 git

11MB 的 `boss-*-log.json`、`51job-*-log.json`、`ab_experiment.db` 全在 git 跟踪里（144 个文件），README 声称"日志在 .gitignore"但实际没有。公司名/岗位/投递时间的求职隐私数据进了版本历史。**建议**：`.gitignore` 补 `*-log.json`、`*.db`、`followups.json`、`data/company_profiles.json`，`git rm --cached` 后提交一次清理。

### A3. 项目有三份拷贝，已开始漂移

- `~/projects/job-hunter`（本仓库，最新：boss_apply.py 8/16 改动）
- `~/.hermes/skills/job-hunting/job-hunter`（HANDOFF 引用的 `tests/regression.py` **只在这里**，本仓库无 tests/）
- `~/.claude/skills/job-hunter`（skill 副本）

改 `shared.py`/`config.json` 后三处行为不一致。**建议**：`~/projects/job-hunter` 为唯一权威目录，另两处改符号链接；或写一个 `deploy.sh` 同步脚本。

### A4. 词表三份、配置漂移

`config.json.job_pools`（64 词）、`ab_test_track.py` L39-50 硬编码 `POOL_KEYWORDS`（另一份）、`config.json.city_pools` vs `ab_test_track.py` `CITY_PRIORITY`（又一份）。实验分组和实际投递词表对不上，实验结论会失真。**建议**：全部从 config.json 读取。

### A5. 熔断策略两处过度反应

1. **日硬熔断触发 = 正常达上限，却关 kill switch**（L1079-1080）：`kill_switch_off` 会禁止**所有**脚本写操作，包括只读 HR 扫描以外的任何后续操作。正常跑满 50 份不该关全局开关，只有"连续失败/风控信号"才该关。
2. **单小时熔断 `sleep(30*60)` 阻塞式长眠**（L1098）：期间 SIGINT 只置 flag 无法中断退出。应改为分段 sleep（如 5s 一段）检查 `SHOULD_STOP`。

### A6. 信号处理过激：一切信号都写暂停锁

SIGHUP/SIGTERM/SIGINT 统一进 `_signal_handler` → `pause()` 写 `.paused`（L100-112）。用户 Ctrl+C 一下，整个自动化（含 launchd 定时）停摆直到手动 `--resume`。**建议**：SIGINT 不写锁（只优雅退出）；仅 SIGHUP（终端/父进程关闭）写锁。

### A7. 投递成功无验证

点"立即沟通"（L724）后不确认会话是否真的打开、消息是否真的发出，直接计 applied（L802）。弹窗文案不匹配、输入框在 iframe/新 tab、按钮 class 变更等静默失败都会被算成"已投"。**建议**：投递后读 DOM 验证（聊天面板可见 / 输入框已清空 / 卡片出现"已沟通"态），失败记 `uncertain` 类目进 failed 日志。

### A8. 巨型 try 包整个投递循环 — boss_apply.py L597-833

任何一步异常都算 failed，只记 `err[:120]` 无 traceback。排障只能靠猜。**建议**：按步骤拆 try（取详情 / 评分 / 点击 / 填话术 / 发消息），异常保留 traceback 到日志，并接入 `failure_tracking.py` 的 apply 阶段枚举。

### A9. 公司去重是精确匹配 — boss_apply.py L84-97

"深圳市XX科技有限公司" vs "XX科技" 算两家 → 跨关键词重复投漏网（正是 8/11 封号的诱因之一）；反过来同名不同城分公司误伤。**建议**：公司名规范化（去 市/区/有限公司/科技 等后缀）+ 规范化后精确匹配，至少命中 7 天窗口。

---

## 四、优化路线图（按优先级）

### P0 — 正确性（1 天内，改动小、见效快）

1. 修 `smart_filter` 的 `desc_lower` / `reason_parts` bug（B1）
2. `--daily` 接线 `job_pools` + `city_pools`（B2）
3. `recent_activity` 改按 entry.time 统计（B3）

### P1 — 投递主链路性能（1-2 天）

4. 日志落盘改 SQLite（或 JSONL 追加），`save_log` 只追加不重写（P1）
5. 日/时熔断统计合并为**单次扫描 + 5 分钟粒度缓存**（P2）
6. 合并 company/salary 的两次 JS 调用（P3）
7. 投递后不整页 reload，同页续投（P4）

### P2 — 工程健壮性（2-3 天）

8. report.py 换真模板引擎 + HTML 转义（B4）
9. 投递成功验证 + 分步异常 + traceback（A7/A8）
10. git 清理：ignore + `rm --cached` 日志与 db（A2）
11. 三副本统一为符号链接，回归测试迁入本仓库（A3）
12. 词表统一从 config.json 读（A4）

### P3 — 风控与体验（择机）

13. 日上限正常结束不关 kill switch；分段 sleep；SIGINT 不写暂停锁（A5/A6）
14. 公司名规范化去重（A9）
15. 清理死代码：`MAX_RETRIES`、`get_today_new`、`hour_now`、`_fetch_company_jobs`、重复 `CITY_CODES`（B5/B6）

---

## 五、值得考虑的产品级方向（非脚本优化，供决策）

1. **手动确认制投递**：脚本每天生成"候选清单 + 打招呼语"（只读），用户在浏览器里自己点。彻底消除封号风险，也符合 HANDOFF 中用户"手动投递 10-20 个/天"的想法。自动脚本保留为可选模式。
2. **Kill Switch 语义分层**：全局只读/只写/全禁三档，避免"日上限跑满"和"封号熔断"混用一个开关。
3. **三线简历实验**（8/14~8/20 因封号中断）：重启前先修 A4（词表统一），否则实验分组与投递词表不一致，结论不可信。
