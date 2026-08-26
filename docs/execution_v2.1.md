# 执行层 v2.1 增量升级说明（Decision Trace / Risk Slowdown / Greeting 埋点）

> 实施日期：2026-08-26。基线：`0d9dc14`（采购专线）。全部走"加字段/旁路模块"，
> 未重构任何现有判定逻辑；正常投递路径行为与 v2 完全一致。

## 提交清单

| commit | 内容 |
|--------|------|
| `f10cc18` | 任务一/二/三代码 + 测试 + 归因规则文档 |
| （本 commit） | 本说明文档 |

**回滚方式**：`git revert f10cc18`（连同本 revert 文档提交一起 revert 即可完全回到
`0d9dc14` 行为；schema 新列留在库中无害，旧代码读到多余列会自动忽略）。

## 任务一：决策链快照（Decision Trace）

做了什么：
- 新增 `decision_trace.py`：`new_trace()` 预填六道门（dedup/smart_filter/deep_filter/
  company_profile/min_score/already_chatted/apply）全部 `not_reached`；
  `gate()` 记录 `pass / rejected:<原因> / not_reached`；`finalize()` 收口
  final_decision/final_reason；`to_json()` 供落库。
- `boss_apply._prepare_job_context` 各过滤点写入 ctx["trace"]；**短路逻辑原样保留**：
  被上游杀掉的岗位不触发下游门（公司背调 API 一个请求不多发），下游门保持 not_reached。
- `_execute_apply` 成功路径收口 trace；异常路径经 `_handle_apply_failure` 补 rejected。
- applied 路径（run_single_cycle 内直接调 record_application 处）同样落库 gates。

动了哪些文件：`decision_trace.py`(新)、`boss_apply.py`、`store.py`
（applications_v2 加 `gates TEXT` 列，events payload 并入 gates JSON）。

验收样例（tests/test_v21_decision_trace.py::TestShortCircuitAcceptance）：
被 smart_filter 杀掉的岗位 gates 为 `{"smart_filter": "rejected:...", "deep_filter":
"not_reached", "company_profile": "not_reached", ...}`。

## 任务二：风控阶梯降速（Risk Slowdown）

做了什么（只加异常路径逻辑，正常路径一行不动）：
- 新增 `risk_slowdown.evaluate(events)` 纯函数状态机：输入本轮事件序列
  ('applied'/'uncertain'/'failed')，输出 `{stop, reason, next_interval_multiplier,
  consecutive_uncertain}`。
- 规则：首次 uncertain → 下一次投递前间隔 ×2（只影响后续等待）；verified 成功 →
  清零恢复正常；连续 2 次 uncertain → pause() 写 .paused **不动 kill switch**，
  打印原因后提前收工；尝试 ≥10 次且 (failed+uncertain)/attempts > 30% → 同上收工。
- 接线在 run_single_cycle 的 `_sd_after_attempt()`：三条出口（明确失败/发送成功/
  异常失败）各记录一次事件并判定。正常路径 multiplier=1.0，
  `time.sleep((1+uniform(0,2)) * 1.0)` 与原节奏逐位一致。
- 参数在 config.json `safety` 段：`uncertain_slowdown_factor=2.0 /
  max_consecutive_uncertain=2 / failure_rate_stop=0.30`，缺字段走 get_safety
  FALLBACK 默认值，老 config 不报 KeyError。

动了哪些文件：`risk_slowdown.py`(新)、`boss_apply.py`。

## 任务三：招呼语模板版本埋点 + 归因规则

做了什么：
- 原 `generate_greeting` 拆为三风格变体 T1 技术栈对齐型 / T2 业务场景型 /
  T3 项目亮点型；bg 取 USER_BG 真实背景、问句取角色词表，绝不编造经历。
- 确定性轮换：`md5(公司名) % 3`（不用内建 hash()，规避 PYTHONHASHSEED 随机化），
  同一岗位重跑拿到同一模板。template id 形如 `T2:AI应用`。
- applications_v2 加 `greeting_template_id TEXT` 列（幂等迁移），applied 落库带上。
- 原 `generate_greeting()` 改为兼容包装（只返回文本），旧调用点零改动。
- `docs/greeting_attribution.md`：HR 回复归因规则——精确匹配优先→归一化包含兜底；
  同公司多岗/匹配不上丢弃不硬凑，宁缺毋滥。本周只定规则，未实现回复抓取。

动了哪些文件：`boss_apply.py`、`store.py`、`docs/greeting_attribution.md`(新)。

## 测试结果（全绿）

| 套件 | 结果 |
|------|------|
| tests/regression.py | 19/19 |
| tests/test_p0.py | 13/13 |
| unittest discover（test_*.py） | 69 OK（基线 31 + 新增 38） |

新增测试文件：
- `tests/test_v21_decision_trace.py`（trace 构建/not_reached 验收样例/store 幂等迁移）
- `tests/test_v21_risk_slowdown.py`（首次降速/成功复位/连续 2 次停/失败率规则）
- `tests/test_v21_greeting.py`（模板 id 确定性/三风格互异/兼容包装）

## 红线自查

- match_engine.py / smart_filter / deep_filter 判定逻辑零改动
  （`git diff 933fed1..HEAD --stat` 无 match_engine.py）；
- 评分算法、min_score 默认值、六道门顺序未动；
- 无新依赖（stdlib only）、无消息队列、无独立协议；
- 所有 schema 变更幂等（PRAGMA 检查列存在才 ALTER，duplicate column 吞掉），
  可重复执行；旧行读出 NULL 不报错。
