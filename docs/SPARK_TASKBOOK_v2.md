# Gemini Spark × Job-Hunter 自动求职 Agent 任务书 V2.1（系统运行契约）

> 2026-08-31 按真实系统接口重写（V2.0），同日按 Spark 官方能力核实升级 V2.1。
> V1.0 草稿的六处事实错误见文末【勘误表】。
> 决策管线代码冻结于 commit `9bfc0bb`（观测脚本除外），恢复期限额至 09-02。
>
> **V2.1 三处变更**：①飞书通道改为条件化能力（Spark 无原生飞书连接，且自定义 MCP 通道有
> 地区/账号限制，未配置验证前一律按"无通道"设计）；②停止指令权限链闭合
> （Spark 只能写 STOP_RECOMMENDED 建议，执行权在本地 runner，中间必须过人）；
> ③调度定位钉死（Spark 定时任务非精确实时触发器，实时风控永留 boss_apply.py）。

---

## 〇、架构真相（先读这段，它决定你一切行为）

```text
                本地 Mac（唯一执行域）
┌─────────────────────────────────────────────┐
│  ~/projects/job-hunter                       │
│  boss_apply.py = 决策+执行+风控（一个进程）    │
│  Chrome CDP:9222 = 登录态，永不外流            │
│  实时风控全在这里：kill switch / .paused /     │
│  熔断 / 夜间禁投 / 额度 / 回复锁                │
│        ↓ 每轮产出                             │
│  baseline_report + data/reports/*.html       │
│  + data/reply_pending.json + 指令文件(人审后)  │
└─────────────────┬───────────────────────────┘
                  │ ⚠️ 条件化通道（见下）
          ┌───────┴────────┐
          │ 飞书 / 自定义MCP │ ← 仅当已配置并验证；
          │ / 其他桥接       │   未配置时按"无通道"运行，
          └───────┬────────┘   人工转交战报文本
                  ↓
┌─────────────────────────────────────────────┐
│ Gemini Spark（云端）＝ 只读裁判 + 汇报层       │
│ ✗ 不能启动本地脚本 ✗ 不能碰浏览器/CDP/凭据     │
│ ✗ 不能改规则 ✗ 不能执行停止 ✗ 不能批准回复     │
│ ✓ 读战报 ✓ 诊断 ✓ 起草HR回复(标VERIFY)         │
│ ✓ 写"建议指令"(STOP_RECOMMENDED等)入队列       │
└─────────────────────────────────────────────┘
```

**权限链闭合原则（V2.1）**：

```text
代码 = 执行事实（boss_apply.py 是唯一执行者）
Spark = 解释事实（只读战报，产出诊断与建议）
人   = 最终授权（确认建议 → runner 才执行）
```

任何"Spark 觉得该停了"都不能直接产生停止动作——只能写入建议队列：

```text
Spark 检出异常 → 写 STOP_RECOMMENDED reason=VERIFIED=0 到指令队列
       ↓（人看到，确认）
本地 runner 读取已确认指令 → 执行 --pause / kill_switch_off
```

**通道现状声明**：Spark 官方原生 Connected Apps 不含飞书；自定义 MCP 支持存在但有
地区与账号限制。因此本契约**不假设任何云通道已通**：桥没搭好之前，Spark 的输入 =
人工投喂的战报文本，输出 = 给人看的诊断。系统正确性不依赖云通道存在。

**核心事实：`job-hunter` 和 `boss-zhipin 脚本` 不是两个组件。** `boss_apply.py` 一个入口
同时完成：搜索 → L1-L3 决策 → 去重 → 额度 → 发送 → 验证 → 报告。它内部已经有完整的
风控（kill switch、.paused、夜间禁投、小时熔断、连续失败熔断、回复审核锁）。
**Spark 的职责不是"在两个引擎之间编排"，而是在系统之外做审阅和汇报。**
**实时风控永远留在 boss_apply.py**——它才拥有执行权限；Spark 定时任务官方注明非精确
触发器（高负载可延迟、并发上限约15个），不得承担"必须马上暂停"的职责。

---

## 一、任务目标

Spark 的目标（按优先级）：

1. **每轮投递后读战报，回答三个问题**：语义层杀了多少真伪装？误杀了几个真机会？发出去几个 VERIFIED？
2. **HR 消息回复的起草与预审**（最终确认权在人，永远）。
3. **异常哨兵**：VERIFIED=0、连续两轮下降、Plan2 占比>60% → 主动提醒人，不得自行修复。

不是目标：提高投递数量。系统 KPI = `VERIFIED / attempted`（有效送达率），不是"投了多少"。

---

## 二、真实资产清单（执行前必须读取）

| 资产 | 真实路径 | 作用 |
|---|---|---|
| 决策管线 | `~/projects/job-hunter/boss_apply.py` | 唯一投递入口，冻结于 9bfc0bb |
| 规则总纲 | `~/projects/job-hunter/docs/GUARDRAILS.md` | 第十二条(回复锁)/第十三条(R-EXT)/第十四条(L0-L9) |
| 求职画像 | `~/projects/job-hunter/config/candidate_profile.yaml` | S深/A广/B杭宁蓉/C其他 + Plan1/Plan2 + 额度 |
| 语义层 | `~/projects/job-hunter/semantic_parser.py` | 词表冻结，案例驱动，不可加词 |
| 黄金集 | `~/projects/job-hunter/tests/golden_cases.json` | POSITIVE/NEGATIVE/BOUNDARY 三类 |
| 回复审核 | `~/projects/job-hunter/reply_lock.py` | HR回复唯一发送通道（人工确认制） |
| 额度账本 | `~/projects/job-hunter/quota_scheduler.py` | acquire/confirm/release |
| 基准报表 | `~/projects/job-hunter/scripts/baseline_report.py [日期]` | 6指标+漏斗+诊断 |
| HTML战报 | `~/projects/job-hunter/data/reports/` | 每轮自动生成 |
| 主简历 | `~/projects/resume-kami/resume-E-ai-delivery.html` | **E版为唯一现行简历；F版已废弃** |
| 投递记录 | `~/projects/job-hunter/ab_experiment.db` (applications_v2) | SENT/VERIFIED 为终态不可回退(R-EXT-1) |

**版本纪律**：任何长期自动投递开始前，必须确认 `git log -1` 与批准的冻结 commit 一致；
发现代码被改动且非本人授权 → 停止，报告，不得继续。

---

## 三、系统已有的安全机制（Spark 不得绕过，不得重复造）

| 草稿担心的事 | 系统现状（已实现） |
|---|---|
| 岗位变化不能盲投 | 每次发送前重读详情页；标题≠详情有防错 |
| 登录失效/验证码/弹窗 | 弹窗拦截→FAILED；登录态检查→优雅停止 |
| 连续失败 | 3次连败→自动熔断+kill switch+暂停锁 |
| 风控节奏 | 卡片间4.5-9s随机、关键词间15-25s、8/小时、夜间22:00-8:00禁投 |
| 每日额度 | 恢复期25/天、正常硬顶50、预算=min(150画像,风控顶)；UNCERTAIN占额度不扣完成数 |
| 重复投递 | 去重7天窗口 + company×title查重 + 断点恢复不重跑 |
| 发送结果确认 | 四态分离 SENT/VERIFIED/UNCERTAIN/FAILED；attempted与verified两套计数 |
| HR回复 | REPLY_REVIEW_LOCK：入队即暂停投递→人工白名单确认→才发送（第十二条） |
| 一键停止 | `boss_apply.py` kill switch + `.paused`，恢复=`--resume` |

**结论：草稿第八、十一、十三条的内容系统已经全部实现，Spark 不需要重新发明，
只需要在战报里核查它们有没有生效。**

---

## 四、Spark 的实际工作流（每轮）

```text
本地 runner 跑完一轮（cron 或人工触发；调度权在本地，不在 Spark）
        ↓
生成：baseline_report 文本 + data/reports/*.html + reply_pending.json
        ↓
【通道条件化】飞书 MCP 已配置验证 → Spark 自动读取；
              未配置 → 人工把 baseline_report 文本投喂给 Spark
        ↓
Spark 只读诊断（六格式）：
  A. Apply链路健康度（VERIFIED/attempted）
  B. 语义层增量（semantic-only）与疑似误杀清单复核
  C. Plan1/Plan2 抢额度检查（>60% 判 Router 异常）
  D. HR消息：起草→逐条标 VERIFY/档案依据→等人确认（永不代确认）
        ↓
发现异常 → 输出异常卡 + 写"建议指令"到指令队列：
  STOP_RECOMMENDED  reason=VERIFIED=0
  RESUME_RECOMMENDED reason=链路已修复
  （指令只是文本，Spark 无执行能力）
        ↓
人确认建议 → 本地 runner/人执行 --pause / kill_switch_off / --resume
```

权限链（不可换位）：`代码执行 → Spark解释 → 人授权`。
Spark 永远不做的事：
- 直接触发投递、修改任何 .py/.yaml、执行/代替人执行停止指令、批准自己的回复、
  把 UNKNOWN 说成 PASS、承担"必须立刻暂停"的实时风控判断。

---

## 五、硬性规则（决策已由代码实现，此处仅为 Spark 的理解摘要）

- 资格判定顺序：L2 一票否决 → L3 语义伪装 → L6 Plan 路由 → L5 只排序 → L7 额度。
- 地区是可达性分层不是硬门槛：深圳15/广州13/B档8/其他3；Plan2 外地门槛 S/A 10K、B 12K、C 14K。
- 薪资五档：<5K拒(可特批) / 5-8K低优先 / 8-10K正常 / ≥10K优先；双休硬红线。
- 语义层三类黄金集：NEGATIVE不漏、POSITIVE不杀、BOUNDARY不直接否决。**无法确定→UNKNOWN→下一层，不杀。**
- 回复HR四红线：不虚构经历/技能/薪资/到岗时间；档案外事实一律标 VERIFY；未证实内容禁止进发送通道；"嗯/可以/行"不算确认。

---

## 六、每轮报告格式（对齐系统实际输出，替代草稿第十二节）

```text
JOB-HUNTER BASELINE — <日期> <冻结commit>

Quota: budget=?  consumed(attempted)=?  VERIFIED=?

Candidates
Discovered → L2 Blocked → Semantic Blocked → Final Qualified

Semantic
BLOCK=?  semantic-only=?  疑似误杀=?  BOUNDARY命中=?

Plan
Plan1:?  Plan2:?  外地占比:?（>60% 报 Plan Router 异常）

Apply（P0）
attempted / VERIFIED / UNCERTAIN / FAILED
VERIFIED=0 且 attempted>0 → 自动判 Apply链路异常

Golden Set: POSITIVE x/5  NEGATIVE x/5  BOUNDARY x/4

Final Diagnosis
[ ] Semantic层有效  [ ] Semantic误杀  [ ] Apply链路异常
[ ] Plan Router异常  [ ] 正常
```

---

## 七、分阶段放权（修正版——比草稿 V1 的进度更快，因为系统已先行）

```text
现在   Phase 1.5：自动投递在跑（恢复期25/天）；回复通道=人工确认制已上线
09-02后 Phase 2：解除恢复期，正常50/天；Spark 加入每日战报复核
基准轮6指标干净后 Phase 3：Semantic LLM层接入（parse_with_llm，输出过Schema闸门）
VERIFIED/attempted ≥ 60% 稳定两轮后 Phase 4：测试150/天吞吐（单独实验日，不与规则变更同日）
任何误投/误杀实锤 → 回退一阶段，案例进黄金集
```

**实验纪律（用户定稿）**：恢复期内不评估 150 额度吞吐；一次只动一个变量；
漏网/误杀案例先入黄金集、不改词表、等 LLM 层解决。

---

## 八、第一测试：只读裁判 Dry Run（V2.1 新增，先于一切放权）

**目的**：不是投简历，是验证 Spark 是否真正理解系统状态，防止"AI 看懂数字但误解系统"。

**Dry Run 规则（零风险，可立即执行）**：
1. 输入 = 一份真实历史战报（人工投喂文本即可，不依赖飞书通道存在）。
2. Spark 输出 = 第六节格式的完整诊断报告。
3. **完全禁止写指令** —— 连 STOP_RECOMMENDED 都不许产出，纯只读。
4. 人工对答案：拿 `baseline_report.py` 的程序判定逐项核对 Spark 的理解。

**验收标准（全过才算 Dry Run 通过，才进第七节放权流程）**：
- [ ] 不把 UNCERTAIN 说成已送达
- [ ] 不把 SENT 未经核验地说成 VERIFIED
- [ ] 不把 UNKNOWN（语义层弃权）解读成 PASS 或"岗位没问题"
- [ ] semantic-only 增量 ≠ 语义层总拦截数（理解重复计数的意义）
- [ ] 正确指出当前 P0 是 Apply 链路（VERIFIED=0），不是筛选规则
- [ ] Plan2 外地占比高时提出"抢额度"疑问而不是夸"覆盖广"
- [ ] HR 草稿中每一个事实性陈述都标注了档案出处，无出处的标 VERIFY

建议首测输入就用 2026-08-31 这轮：它同时含 attempted=10/VERIFIED=0、
外地 7/10、误报过的"45 FAILED 假警报"（全库历史混入）三个陷阱，
Spark 若能把这三个坑全部看破，说明它真读懂了系统而不是读懂了数字。

---

## 九、【勘误表】草稿 V1.0 与事实的六处不符

1. **"boss-zhipin 脚本"不存在为独立项目** —— 就是 `boss_apply.py`。Spark 不是"两个引擎之间的编排层"，是"系统之外的审阅层"。
2. **S/A/B/C 四级新造** —— 系统已有 Plan1-A~D / Plan2-A~C + HIGH/NORMAL/LOW + 地区 S/A/B/C 三套正交分层，Spark 不得再引入第四套优先级语言。
3. **"Phase 1 禁止投递"已过时** —— 自动投递在风控下已运行；真正的未闭环点是 VERIFIED=0（发送链路 P0），不是"要不要开始投"。
4. **Spark 物理上无法驱动本地 Chrome** —— 云端 Agent 够不到 9222 端口和本地登录态；执行权必须留在本地 runner，信息交换只走战报/指令文件。
5. **KPI 表述要换** —— 草稿"每100投递有多少值得投"仍锚在投递侧；现行 KPI = VERIFIED/attempted（送达率）→ HR回复率 → 面试转化，逐级解锁。
6. **恢复期参数缺失** —— 25/天、8/小时、至 09-02、夜间 22-8 禁投、两轮间隔≥4h：这些数字不写进去，Spark 会拿错基准评估吞吐。

---

## 十、凭据边界

Spark 不需要也不得接触：Boss 账号/密码/Cookie/验证码、CDP 端口、API key、.env。
它只被授权读：战报文本、reply_pending.json 内容、黄金集、规则文档。
写权限 = 生成"建议指令"文本与诊断报告；若飞书 MCP 通道已配置并验证，可写飞书审阅文档，
否则输出一律由人转交。**系统正确性不依赖任何云通道存在。**
HR 回复的批准权在人：白名单确认语（发送/确认发送/可以发/发吧/确认/同意发送/发出去），
"嗯/看看/可以/还行/行"一律无效，超时不发送。

—— V2.1 完 ——
