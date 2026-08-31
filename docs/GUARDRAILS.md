# GUARDRAILS v2.0 + VSCORE — L0~L3 确定性岗位决策系统（2026-08-31 定稿，RULES_v2.0）

**定位**：换任何模型/任何人，投递决策走固定五步，职责不串线。
**核心公式：先判不能投什么（L1），再判能不能投（L2），最后判最值得投什么（L3）。**
**1万不是规则，8千也不是规则——L1/L2 是资格，L3 才是排序。模型只负责理解信息。**

## 总架构（用户定稿，勿回退）

```
L0  数据采集（Boss列表/详情 → 结构化信号 salary/workday/shift/intern/outsourcing/travel/role_type）
      ↓
L1  Hard Guardrails（guardrails.py，启动强制）  违规 → 拒绝启动投递
      ↓
L2  Job Decision（job_decision.py，资格层先行） 不符资格 → REJECT
      ↓
L3  Value Score（value_score.py，确定性排序）   0-100 → HIGH/NORMAL/LOW
      ↓
投递排序 / 优先级
```

**架构不变量（tests/test_architecture.py 10 例锁死，违反 = 测试红 = 不许合）**：
1. ALLOW/REJECT 只来自 L2 `Decision.action`；L3 的 ValueScore **没有 action 字段、代码级不出现 REJECT 字样**
2. boss_apply 主流程中 L3 结果从不进入 `if vs.score < N` 比较、从不触发 `return "skip"`
   → 永远 `if decision.action == "REJECT": do_not_apply() / elif ALLOW: rank_by(value_score)`，
   绝不 `if value_score < 60: reject()`（防 L3 偷偷变成第二个 L2 的退化）
3. 权重版本化：改 WEIGHTS → `VSCORE_VERSION` +0.1 → 跑 `scripts/vscore_benchmark.py` 对比 v1.0 基线；
   变化必须人话可解释，解释不了 → 回退。基准岗位集 `tests/benchmark_roles.json` 不可删除。

## 第一层：Hard Guardrails（代码强制，全部不可由模型决定）

`guardrails.py` 启动校验，违反 → 拒绝启动：

| # | 红线 | 校验内容 |
|---|------|----------|
| 1 | 夜间禁投 | `night_ban_start ≤ 22`, `night_ban_end ≤ 8` |
| 2 | 日/时上限 | 日 ≤100, 时 ≤15 |
| 3 | 分层薪资参数 | `hard_floor ≥5K`, `normal_floor ≥8K`, `priority ≥10K`（可更严不可更松） |
| 4 | 双休词表 | 正文排除词必须含：单休/大小周/996/夜班 |
| 5 | 实习词表 | 标题排除词必须含：实习/实习生 |
| 6 | 决策器完整 | `job_decision.py` 关键常量/函数存在，词表未被抽空 |
| 7 | kill switch / .paused / 熔断 | （S1/S2，原有代码强制） |

**注意 v2.0 变化**：① 旧版校验 `home/away_min_accept ≥ 10`（1w 硬线）**已退役**——
那是过时规则。② 新增 B 层校验：岗位价值决策器本身不可被删改。

## 第二层：Job Evaluation（岗位价值决策器 `job_decision.py`，确定性规则）

**架构：模型/解析层负责「看懂」，决策器负责「判死刑」**——零 LLM，纯规则。

```
岗位原始信息(company/title/desc/salary)
      ↓
模型结构化解析(可选) parsed_signals={workday, shift, intern, outsourcing}
      ↓
job_decision.evaluate_job()  ← 确定性规则
      ↓
ALLOW / REJECT (+优先级 HIGH/NORMAL/LOW)
```

**薪资分层（用户定稿，勿回退）**：

| 薪资带 | 决策 | 备注 |
|--------|------|------|
| <5K | REJECT（默认） | 编制/国企/极高稳定可特批 |
| 5-8K | 特批通道 | 命中编制/国企/正式工/高稳定/轻松 → ALLOW(LOW)，否则 REJECT |
| 8-10K | ALLOW(NORMAL) | 正常可接受 |
| ≥10K | ALLOW(HIGH) | 高优先级 |
| 未知/面议 | ALLOW | **不因薪资未知拒绝**（由其他维度决定） |

**制度红线（任何薪资都生效，命中即死）**：单休/大小周/996/007/上六休一/轮班/倒班/
三班倒/夜班/月休4天/每周休1天。**实习岗/人力代招主体 → REJECT。**

**特批信号**（5-8K 破例通道）：编制/国企/央企/事业单位/正式工/正式员工/五险一金齐全/
六险二金/稳定/轻松/加班少/不加班/南方电网/国家电网/中石油/中石化/铁路局/烟草。

**验证过的关键案例**（tests/test_job_decision.py）：
- 南方电网 5-7K 正式编制 → **ALLOW(LOW特批)** ✅（低薪但高质量必须走正式通道，不靠模型临场）
- 12-16K 外包驻场大小周 → **REJECT**（制度红线，高薪也死）✅
- 9K AI实施双休 → ALLOW(NORMAL) ✅
- 薪资面议 Agent 岗 → ALLOW(unknown) ✅（不因未知拒绝）
- 私企 6-8K 行政（大小周）→ REJECT（制度优先）✅

## 第三层：岗位价值评分器 `value_score.py`（VSCORE v1.0，只排序不拦截）

```
岗位信息 → L1/L2 资格裁决通过
      ↓
value_score()  ← 确定性规则，零 LLM
  ├─ 薪资 30
  ├─ AI匹配度 25（复用 explain_match 技术+方向加权）
  ├─ 工作制度 10（双休信号）
  ├─ 稳定性 10（编制/国企/央企/上市公司）
  ├─ 技术成长 10（AI核心职责/AI实施/弱AI）
  ├─ 福利 5（五险一金/公积金/年终奖）
  ├─ 城市 5（home城市 优先）
  └─ 工作强度 5（不加班/加班少）
      ↓
0-100 分 → HIGH(≥80) / NORMAL(60-79) / LOW(<60)
      ↓
投递优先级（进 reason 落库：|价值92分[HIGH]）
```

**排序语义（tests/test_value_score.py 9 例验证，勿回退）**：
- 12K AI应用双休 92分 > 8.5K AI应用双休 84分 > 南方电网5-7K编制 63分
- 8.5K AI应用双休五险一金 84分 > 12K 外包驻场 63分（同薪 AI匹配度拉开）
- 客服 8-10K → LOW；标注 <5K → 资格层 REJECT（评分器不背锅）
- 薪资面议 Agent岗 → ALLOW + ≥60分（不因薪资未知淘汰）

**边界**：L3 只排序不拦截；REJECT 永远归 L2（job_decision）。L3 异常降级为跳过评分，绝不阻断投递。

## 第十二条：HR 回复优先级规则（v5 最高优先级交互规则，2026-08-31 用户定稿）

**回复消息 ≠ 自动投递。所有"回复 Boss/HR"的行为优先级高于"投递简历"。**
本条是 **Scheduler/Orchestrator 层的任务调度规则**（`reply_lock.py` 文件锁实现），
不是 LLM prompt 规则——模型抽风也绕不过代码锁。

```text
检测到需回复会话（HR主动发消息/问经历薪资到岗/邀请继续沟通）
  → acquire() 上 REPLY_REVIEW_LOCK + 拟回复入队
  → 投递 worker 在 (城市×关键词) 任务边界检测到锁 → 保存断点退出
  → 人工审核：展示 HR原消息/上下文/拟发送/目的
  → 明确确认才发送；未确认/拒绝 → 不发送
  → 队列清空 → 释放锁 → 从断点恢复投递（不重新初始化整轮、不重复消耗额度）
```

**确认语义（代码级白名单，`reply_lock.is_confirmation`）**：
- ✅ 发送 / 确认发送 / 可以发 / 发吧 / 确认 / 同意发送 / 发出去
- ❌ 嗯 / 看看 / 可以 / 还行 / 行 / 没问题吧 / 应该可以 —— 语义不明确一律等待
- 禁止默认同意、禁止超时自动发送、禁止"自动修改后发送"；改草稿后重新进待审核

**回复质量红线（生成侧）**：不虚构经历/项目/技术/薪资/离职原因/到岗时间；
不承诺用户未确认的事项；准确回答 HR 问题优先于讨好。

**投递任务处理**：APPLY → REPLY_REVIEW →（完成）→ NONE → 恢复 APPLY。
断点 `data/queue_checkpoint.json` 记录已完成的 城市×关键词；HR 回复永不计入投递数量。

**双轨统计（相互独立，`reply_lock.stats()`）**：
- 投递侧：applications_attempted / verified / uncertain / failed（额度=attempted，成功=verified）
- 回复侧：hr_messages_detected / drafted / confirmed / sent（不占投递额度）

**核心原则：生成回复可以自动化，发送回复必须人工确认。**
系统可以替使用者思考，不能替使用者向 HR 做未经确认的承诺。

实现清单：`reply_lock.py`（锁/队列/断点/统计/CLI）；`hr_auto_reply.py`（扫描+起草后只入队，
`--send`/`--watch` 自动发送后门已拆除）；`boss_apply.py` 主循环任务边界查锁+断点推进。

## 第十三条：外部规则基线（R-EXT 系列，2026-08-31 考古验证后采纳）

> 来源与验证记录见 `docs/external_rules_baseline.md`。核心原则：**模型可以不同，但 Schema 和 Rule Engine 不能不同。**
> R-EXT-1/2 立即生效；R-EXT-3/4/5/6 随 v5.2 Semantic Parser 阶段落地，落地一条补一条测试。

- **R-EXT-1 状态机保护**（mattohan567）：`applications_v2` 中 SENT/VERIFIED 为终态，
  禁止批量回退状态或破坏性删除；只允许归档。投递记录是审计资产，不是临时缓存。
- **R-EXT-2 VERIFY 规则**（mattohan567 + Malik1942 "filler never guesses"）：
  简历/候选档案之外的事实性陈述（项目、技能、薪资、到岗时间）一律标 `VERIFY`，
  **never transmit it**——HR 回复草稿含未证实陈述时禁止进入发送通道，必须人工补证或改写。
- **R-EXT-3 Schema 约束生成**（pseudog0d）：LLM 输出必须匹配 `job_schema.py` 固定 Schema，
  解析校验通过后才可进 Hard Gate；格式错误 → 重试/切备用供应商，绝不静默接受脏数据。
- **R-EXT-4 面试可辩护原则**（pseudog0d）：招呼语与回复中的每条陈述必须 interview-defensible；
  候选人不具备的技能用真实相邻证据表达，禁止虚构直接经验（与第十二条回复质量红线同源）。
- **R-EXT-5 Coverage Gate（修正版，用户定稿）**：只用于**生成内容**（招呼语/回复），
  **绝不用于投递资格**。检查"是否覆盖 JD 重要信息"，但 **VERIFY Gate（R-EXT-2）拥有最终否决权**：
  覆盖率不足 → 只用候选人真实存在的证据重新生成；**禁止为凑覆盖率逼模型虚构**
  （"我具备 Agent 项目经验"这类补句 = 幻觉制造器，一律拦截）。
  顺序：Coverage Gate → VERIFY Gate → Human Approval。
- **R-EXT-6 Provider Failover（修正版，用户定稿）**：只允许发生在**传输层**——
  超时 / 限流 / 网络错误 / 服务不可用 / Schema 无法生成。**禁止**因"模型给了不喜欢的结果"而切换重试；
  否则多模型容错会退化成多模型投票作弊（找到 PASS 为止 = 决策漂移）。
  备用通道（deepseek → qwen）只换传输，不换裁判：Schema 和 Rule Engine 永远不变。

## 第十四条：L0-L9 决策管线层级（用户定稿，2026-08-31，架构不可变梯度）

```text
L0  Candidate Profile   用户长期偏好（不可变层）
L1  Normalize           JD → Schema（job_schema.py，坏数据→UNKNOWN）
L2  Hard Gates          一票否决（guardrails + job_decision，确定性）
L3  Semantic Parser     识别隐藏语义（semantic_parser.py，标题≠实际岗位类型）
L4  Evidence / VERIFY   事实是否可证明（R-EXT-2，未证实不进发送通道）
L5  Value Score         只负责排序（value_score.py，权重可 A/B）
L6  Plan Router         Plan1 / Plan2（确定性路由）
L7  Quota Scheduler     每日额度（quota_scheduler.py）
L8  Action              投递 / 回复（回复走第十二条人工确认锁）
L9  Verification        SENT / VERIFIED / UNCERTAIN / FAILED
```

**架构原则：越靠前越"不可变"，越靠后越"可调整"。**
- 不可变（换模型不许变）：L0 画像、L2 一票否决、L1 Schema 结构、L4 VERIFY 否决权
- 可调整（按节奏迭代）：L5 权重（A/B）、L6 关键词优先级（可学习）、地区档位

**新旧层级映射**（旧文档的 L0-L3 以此为准翻译）：
旧 L0 数据采集 → 新 L1；旧 L1 Hard Guardrails + 旧 L2 Job Decision → 新 L2（一票否决合并）；
旧 L3 Value Score → 新 L5；L3 Semantic Parser 为 v5.2 新增，插在资格层与排序层之间。

**开发方式（用户定稿）**：新增规则 = 正例 + 反例 + 边界案例 → 全量回归 → 全过 → 提交。
不再"改代码跑一下感觉没问题"。黄金回归集 `tests/test_semantic_parser.py` 中的漏网案例永不复漏。

## 修改协议（改规则必须三步同步，禁止只改一处）

1. `job_decision.py`（分层常量/特批词表）或 `guardrails.py`（系统红线）
2. `tests/test_job_decision.py` + `tests/test_guardrails.py`（改松必须能拦住）
3. 本文件

改完跑：`test_job_decision`(16例) + `test_guardrails`(11例) + 全量 103 例。

## 历史变更

- v1.0 (2026-08-31)：初始固化，1w 硬线校验（**后被 v2.0 判定为过时规则**）
- v2.0 (2026-08-31)：薪资从「10K 硬线」升级为「五档分层+特批通道」；
  新增 B 层决策器完整性校验；新决策器 `job_decision.py` 接入主流程（资格层先行）。
  触发事件：用户指出固化规则 = 旧规则（1w 硬线 ≠ 新分层），且「验词表≠验岗位」。