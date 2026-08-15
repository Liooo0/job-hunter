# Job Hunter 项目交接文档（Hermes → Claude Code）

**交接时间**：2026-08-15
**交接人**：Hermes Agent（刘文迪的 AI 助手）
**接手人**：Claude Code
**项目路径**：`/Users/liuwendi/projects/job-hunter`
**Git 仓库**：已初始化（main 分支，6 个 commit）

---

## 一、当前状态（接手时的第一现场）

| 项 | 状态 |
|----|------|
| **Boss 账号** | ❌ **封禁中**（8/15 16:00 左右被平台封禁，时长未确认） |
| **暂停锁** | `.paused` 存在：`{"reason": "2026-08-15 再次封号(86投5小时超速),暂停全部直到解封"}` |
| **Kill Switch** | `.kill_switch` = `{"enabled": false, "reason": "2026-08-15 再次封号,禁止一切投递"}` |
| **投递 cron** | paused（job_id: 076fc3338e0e，8:00 投递窗口） |
| **收尾 cron** | paused（job_id: af4acbbe7de5，18:00 收尾） |
| **HR 扫描 cron** | **enabled**（job_id: 76477697a4db，12:00/18:00，只读分类不发送） |
| **跟进提醒 cron** | **enabled**（job_id: 490681d9f923，9:00，只读清单） |
| **Chrome 9222** | 可能还开着（投递专用 profile: ~/job-hunter-chrome） |

**接手后第一件事**：确认账号封禁状态（打开 Boss 页面看是否提示），**不要恢复任何投递**。用户（刘文迪）明确表示暂时不信任 Hermes 操作投递，希望 Claude 接手评估。

---

## 二、封号时间线（全部事实，两次封号）

### 第一次封号：2026-08-11 投递 → 8/12 封 2 天

**经过**：
- 8/11 一天跑了 4 轮投递进程接力（10:41/14:00/16:00/21:00），共投 191 份
- 21:00-23:00 三小时狂投 114 份（22 点单小时 47 份），进程跑到凌晨 04:40
- 8/12 早发现被封，封 2 天（约 8/14 23:50 解封）

**根因**：
1. 为满足"投满 150"目标，多轮进程接力（每轮进程内计数独立，互不知道对方投了多少）
2. 夜间（22:00-08:00）持续运行，Boss 夜间风控严格
3. 单小时 47 份远超人类节奏

### 第二次封号：2026-08-15 解封当天 → 再封

**经过**：
- 8/14 23:50 解封（用户确认）
- 8/15 11:08 用户说"投一下"，Hermes 启动投递：`--cities 9城 --jobs 61关键词 --count 30`
- 进程从 11:10 跑到 16:00（5 小时），实际投出 86 份（深圳34/广州26/惠州20/佛山6）
- 单小时分布：11点17 / 12点20 / 13点23 / 14点6 / 15点7 / 16点13
- 16:07 用户发现"又被封了"，进程被杀

**根因（致命）**：
1. **`--count 30` 语义误判**：`--count N` 是"每个城市×每个关键词"的上限，**不是本轮总量**！61 关键词 × 9 城市 = 549 格，理论上可投 549×30。Hermes 误以为 30 是总量，实际当天投了 86 份（还没投满格子就封了）
2. **启动后没有盯进程**：5 小时无人监控，单小时 13-23 份波动，超 15/小时红线
3. 解封当天就投 86 份——账号刚解封风控分高，应该降量（30-50 份/天）观察

**教训（写进 skill，接手人必须遵守）**：
- `--count` ≠ 总量！真实受控靠脚本内"跨进程每日硬熔断"（读日志统计当日总量 ≥100 即停）+ "单小时熔断"（≥15 休息 30 分钟）
- 任何投递启动前：date 查时间 + 查 .paused / .kill_switch + 查今日日志已投数
- 启动后每 30 分钟 poll 一次，不能启动就不管

---

## 三、项目架构（V1 加固后）

### 主目录文件

| 文件 | 功能 | 备注 |
|------|------|------|
| `boss_apply.py` | 主投递脚本（搜索→筛选→投递→日志） | 含风控熔断，**核心** |
| `shared.py` | 公共库：score_jd 评分 / smart_filter 过滤 / Kill Switch | **决策核心之一** |
| `deep_filter.py` | 深度筛选：标题党/AI包装/公司背调/实习薪资陷阱 | **决策核心之二** |
| `ab_test_track.py` | 7天实验漏斗（投递→已读→回复→面试→offer） | SQLite: ab_experiment.db |
| `failure_tracking.py` | **S5 新增**：失败原因数据层（23 枚举+9 阶段+实验汇总） | 新表 failure_reasons |
| `hr_auto_reply.py` | HR 消息分类+建议回复（默认预览不发送，`--send` 才发） | 只读安全 |
| `hr_cleanup.py` | 拒绝消息归档（先写后删+分类依据落盘） | 被 hr_auto_reply 引用 |
| `follow_up.py` | 3-14 天跟进提醒（只读） | |
| `report.py` | 终端/HTML 报告 | |
| `config.json` | 所有筛选规则（见下） | |
| `test_failure_tracking.py` | S5 测试（9 项全过） | |

### archive/（已归档，不执行）

- `archive/legacy/`：boss_full.py（旧投递）、boss_reply.py（旧回复）、probe_boss.py（会话探测）
- `archive/platforms/`：51job/liepin/zhilian/yupao/all_apply（其他平台，当前策略只用 Boss）

### 已删除（永不再用，勿恢复）

- `migrate_cookies.py`、`clear_risk_cookies.py`——**对抗风控的工具**（换壳绕封），8/12 删除。这类脚本是封号加速器，**不要重新写**。

---

## 四、config.json 关键配置

```json
min_score: 10（很宽松，主要靠排除词）
body_exclude_keywords: 44 个（销售/教培/单休/大小周/996/出差/轮班等，8/12 补了24个）
job_pools.keywords（v5 收敛版，5 组）:
  S级-AI实施/解决方案（12词）: AI实施工程师/解决方案/RPA实施/数字化实施...
  S级-AI应用工程师（12词）: AI应用/LLM应用/Agent应用/RAG/Dify/Coze...
  A级-车联网/智能汽车（23词）: 车联网测试/车载测试/智能座舱/OTA/ADAS/CAN/HIL...
  A级-IT技术支持/数字化（12词）: IT support/helpdesk/技术支持/技术助理...
  B级-AI内容运营（5词）: AI产品运营/AI内容运营/AIGC运营...
city_pools.city_priority: 深圳=primary / 广州佛山惠州东莞=secondary / 沪苏杭京=opportunistic
```

**简历三版本**（~/Documents/求职资料/简历/）：
- `AI应用工程师_A版.pdf` → 投 AI应用/Agent/RAG 岗
- `AI解决方案工程师_B版.pdf` → 投 AI实施/解决方案/技术支持 岗
- `车联网测试_C版.pdf` → 投 车联网/智能座舱/OTA 测试岗

---

## 五、核心机制（接手后必读）

### 1. Kill Switch（S1，8/13 新增）

```bash
python3 boss_apply.py --kill-status   # 查看
python3 boss_apply.py --kill-off "原因"  # 关闭（禁一切写操作）
python3 boss_apply.py --kill-on        # 恢复
```
状态文件：`.kill_switch`。所有写操作前检查。

### 2. 熔断器（S2，8/13 新增 + 8/15 加强）

- 连续 3 次失败 → 自动关 kill switch + 写暂停锁 + 停
- **跨进程每日硬熔断**：读所有 boss-*-log.json 统计今日已投 ≥100 → 自动停（不依赖 --count）
- **单小时熔断**：当日该小时已投 ≥15 → 休息 30 分钟

### 3. 暂停锁（.paused）

- 存在即投递脚本拒绝运行
- 恢复：`python3 boss_apply.py --resume`（**封号期不要恢复**）

### 4. dry-run 演练模式

```bash
python3 boss_apply.py --dry-run --cities 深圳 --jobs "AI实施工程师"
```
只生成计划 JSON，不碰浏览器/账号。**改筛选规则后先跑这个**。

### 5. 回归测试（18 例全过）

```bash
PYTHONPATH="" /usr/bin/python3 ~/.hermes/skills/job-hunting/job-hunter/tests/regression.py
```
真实 JD 案例 18 个（8 pass / 10 reject），覆盖销售包装/教培/高薪实习/算法岗/大小周/人力代招等。**改 shared.py/deep_filter.py/config.json 后必须跑**。

### 6. S5 失败原因追踪

```bash
python3 failure_tracking.py record <job_id> <stage> <reason> [--resume 版本]
python3 failure_tracking.py stats / funnel / experiment
python3 test_failure_tracking.py   # 9 项测试
```
枚举：9 个 stage（apply/viewed/contacted/replied/resume_sent/interview_1/interview_2/offer/closed）+ 23 个 reason。

### 7. HR 消息（只读安全模式）

```bash
python3 hr_auto_reply.py        # 预览分类（不发送！）
python3 hr_auto_reply.py --send # 才发送（≤5条/次，30-45s间隔）
python3 hr_cleanup.py --list    # 归档预览
```

---

## 六、Git 历史（6 commits，按时间）

| commit | 内容 |
|--------|------|
| `371d6f0` | baseline: V1 结构整理（archive 分离 + 删危险脚本） |
| `a93959f` | V1 加固：dry-run/日志字段/回归测试 18 例/修复 3 漏洞 |
| `cbcdba9` | 审查修复：实验窗口顺延 8/14~8/20/收尾 cron 暂停/简历版本入漏斗 |
| `5a33e21` | S1 Kill Switch + S2 连续失败自动熔断 |
| `48e581e` | S5 failure reason tracking（9 测试全过） |
| `ca51ee0` | 跨进程每日硬熔断(100)+单小时熔断(15)+kill switch 联动 |

---

## 七、cron 任务清单（Hermes 侧）

| job_id | 名称 | 调度 | 状态 |
|--------|------|------|------|
| 076fc3338e0e | 投递窗口-保活+自动投递 | 每天 8:00 | **paused** |
| af4acbbe7de5 | 投递窗口-收尾 | 每天 18:00 | **paused** |
| 76477697a4db | HR消息扫描-分类汇总 | 每天 12:00/18:00 | enabled（只读） |
| 490681d9f923 | 投递跟进提醒-3天窗口 | 每天 9:00 | enabled（只读） |

注：这些是 Hermes 的 cron（cronjob 工具管理）。若 Claude 接管投递，需协调避免双跑。

---

## 八、飞书通知（job-notify）

- 独立飞书应用 `job-notify`（appId: cli_aaf19c2bac389bc9），与 f1501（装修/文档）隔离
- 推送命令：`lark-cli profile use job-notify && lark-cli im +messages-send --user-id "ou_faf2d95b28cd11eb6d6ef39a1248bf47" --text "..."`

---

## 九、安全边界（用户明确要求，勿违反）

1. **不写对抗风控的脚本**（cookie 迁移/清除、换 profile、改 UA 指纹等）——已删，勿重建
2. **投递量铁律**：日 ≤100 分 2 轮 / 单时 ≤15 / 禁夜间 22:00-08:00 / 禁多轮接力
3. **--count 语义陷阱**：`--count N` = 每城市×每关键词上限，不是总量！用脚本内硬熔断控制
4. **解封后降量 3 天**（30-50 份/天），无异常再恢复
5. **HR 消息判断权在用户**：脚本只分类预览，`--send` 需用户明确授权
6. **封号期**：不碰 Boss，冷却账号
7. 用户目标：**尽快找到工作 → 提车（FL5）**。薪资底线到手 1 万。当前求职验证期（三线简历实验 8/14~8/20 因封号中断，需重新规划窗口）

---

## 十、待 Claude 决策的事项

1. **账号封禁现状**：确认封多久、何时解封（用户说 8/12 封 2 天、8/15 再封，时长未确认）
2. **投递模式**：用户表示考虑"手动投递 10-20 个/天"替代自动脚本——Claude 应评估是否值得继续自动化
3. **实验窗口**：8/14~8/20 三线简历实验因封号中断，需重定窗口
4. **S4（HR 状态机）**：暂缓，等实验数据跑完再设计
5. **cron 协调**：Hermes 的 cron 是否保留/暂停/转移

---

*本交接文档由 Hermes Agent 于 2026-08-15 生成。所有事实来自项目 git 历史、日志文件、cron 状态和 skill 记录。*
