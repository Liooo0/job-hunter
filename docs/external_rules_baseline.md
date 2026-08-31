# 外部规则基线 — job-hunter v5.2（2026-08-31 考古 + 验证）

> 目的：停止"遇到一个坑加一个 if"，改为从已验证的外部项目选择性移植规则。
> 本文件记录每个来源仓库的**验证状态**和**采纳决定**；被采纳的规则正文进 `docs/GUARDRAILS.md` 第十三条（R-EXT 系列）。
> 原则（用户定稿）：**模型可以不同，但 Schema 和 Rule Engine 不能不同。**

## 来源验证记录（2026-08-31 经 GitHub API + 原文拉取核实）

| 仓库 | 状态 | 判定 |
|---|---|---|
| `mattohan567/job-application-agent` | ✅ 真实，4⭐，最后推送 2026-08-14 | **采纳**（状态机保护、VERIFY 规则、提交确认） |
| `pseudog0d/job-apply-agent` | ✅ 真实，README 原文核实 | **采纳**（Schema 约束、反幻觉、coverage gate、provider failover） |
| `ajaykallepalli/resume-agent` | ✅ 存在但 0⭐、2026-03-30 停更 | **仅参考**（Monitor→Score→Apply→Outreach 流水线，本系统已具备） |
| `YIKUAIBANZI/job-hunter` | ✅ 真实，27⭐，Boss/51job/猎聘/鱼泡 | **仅参考**（规则深度不及本系统：exclude_keywords + min_score，无资格层/评分层） |
| `Malik1942/job-agent` | ✅ 存在 | **概念并入**（"filler never guesses" = VERIFY 规则同源） |

⚠️ 转述中"710 岗位/491 提交/14 面试"为 mattohan567 作者自述，未独立核实，不作为性能依据。

## 逐项对照：已有 / 采纳 / 不做

### 已有（不需要移植，只记录对齐）
- 确定性预筛在 LLM 之前（pseudog0d phase1 = 本系统 L1/L2 先于 L3 语义）✅
- 人工确认才提交（pseudog0d review dashboard = 本系统第十二条 REPLY_REVIEW_LOCK）✅
- 状态追踪（本系统 applications_v2 的 SENT/VERIFIED/UNCERTAIN/FAILED）✅
- 每日额度上限（pseudog0d 无，本系统反而更强：150 预算 + 风控硬顶双保险）✅

### 采纳（写进 GUARDRAILS 第十三条，R-EXT-1 ~ R-EXT-6）
1. **R-EXT-1 状态机保护**（mattohan567）：终态记录（applied/interviewing/rejected）禁止批量回退或破坏性清理，只允许归档。→ 映射：applications_v2 中 SENT/VERIFIED 记录不可批量删除或状态回退。
2. **R-EXT-2 VERIFY 标记**（mattohan567 + Malik1942）：未经证据支持的事实一律标 `VERIFY`，"never transmit it"。→ 映射：HR 回复草稿中出现简历/档案之外的事实性陈述，必须标注或拦截，不得直接进发送队列。**P0 级（用户 2026-08-31 定序）。**
3. **R-EXT-3 Schema 约束生成**（pseudog0d）：LLM 输出必须匹配固定 Schema，解析校验后才可用；格式错误 → 重试或降级，绝不静默接受。→ 映射：`semantic_parser.validate_parse_output()` 已落地（v5.2，坏数据 → UNKNOWN 不放行）。**P1 级。**
4. **R-EXT-4 面试可辩护原则**（pseudog0d）：生成的每条陈述必须"interview-defensible"；候选人不具备的技能用真实相邻证据表达，禁止虚构直接经验。→ 映射：招呼语与 HR 回复生成规则；semantic_parser 拦截必须附 evidence。
5. **R-EXT-5 Coverage Gate（2026-08-31 用户修正）**：只用于生成内容，**不用于投递资格**；VERIFY Gate 拥有最终否决权，禁止为凑覆盖率逼模型虚构。顺序：Coverage → VERIFY → Human Approval。
6. **R-EXT-6 Provider Failover（2026-08-31 用户修正）**：只允许传输层故障切换（超时/限流/网络/服务不可用/Schema 无法生成）；**禁止**因结果不满意而换模型重试（防多模型投票作弊）。

### 不做（现在不动）
- **目录重构**（rules/decision/scheduler/apply/reporting 五段式）：方向认可，但当前 P0 是发送链路验证，重构属于大手术，等 Semantic Parser 落地后随 v5.2 收口一起做，避免中途改结构打断测试基线。
- YIKUAIBANZI 的 config 结构：本系统 `candidate_profile.yaml`（四档地区 + Plan 分层 + 信号词表）已覆盖且更深。

## 与现有 172 项测试的关系
- R-EXT-1/2/3/4/5/6 目前为**规则入档**，对应代码实现随 v5.2 Semantic Parser 阶段落地；
- 落地一条补一条测试，纳入"修改协议"三步同步。
