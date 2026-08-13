# S5 — 失败原因追踪（Failure Reason Tracking）

## 目的

为 job-hunter 增加「可用于实验分析」的失败原因数据层，服务 8/14~8/20 三线简历实验。

核心价值：把「没回复」这个无价值信息，细化为「查看后无回复 / 简历后拒绝 / 技术不足 / 薪资不匹配」等**可训练的信号**，让实验能回答「哪一版简历带来面试」而不是「哪一版回复率高」。

## 三概念严格区分

| 概念 | 含义 | 例子 |
|------|------|------|
| `status` | 当前招聘状态（applications 表已有字段） | closed |
| `failure_stage` | 失败发生在哪个阶段（固定枚举） | resume_sent |
| `failure_reason` | 为什么失败（固定枚举） | resume_rejected |

不要把「简历拒绝」直接写成 status。

## failure_stage 枚举（9 个）

`apply` `viewed` `contacted` `replied` `resume_sent` `interview_1` `interview_2` `offer` `closed`

## failure_reason 枚举（23 个）

- 投递阶段：`apply_failed` `duplicate` `position_closed` `invalid_position`
- 查看阶段：`viewed_no_reply` `viewed_timeout`
- HR联系阶段：`contact_no_reply` `contact_rejected`
- 简历阶段：`resume_rejected` `resume_no_reply`
- 面试阶段：`interview_1_rejected` `interview_2_rejected` `technical_gap` `experience_gap` `salary_mismatch` `culture_mismatch`
- Offer阶段：`offer_rejected` `offer_salary_low` `offer_other`
- 其他：`candidate_withdrew` `company_withdrew` `suspected_scam` `other`

## 数据字段

`job_id` `company` `position` `resume_version` `failure_stage` `failure_reason` `source` `note` `created_at` `updated_at`

落点：复用 `ab_experiment.db`（SQLite），新建 `failure_reasons` 表。不新增平行数据库。

## 查询接口

```python
from failure_tracking import (
    record_failure, get_failure_reasons, get_failure_stats,
    get_failure_funnel, get_resume_experiment_summary,
)

# 记录（幂等：同 job_id+stage+reason 不重复写入，只更新 updated_at）
record_failure(job_id, "resume_sent", "resume_rejected", resume_version="B-解决方案", note="...")

# 查询
get_failure_reasons(job_id=None)
get_failure_stats(resume_version=..., failure_stage=..., failure_reason=..., start_date=..., end_date=...)
get_failure_funnel(resume_version=..., start_date=..., end_date=...)

# 三线简历实验汇总（重点）
get_resume_experiment_summary("2026-08-14", "2026-08-20")
```

## CLI 用法

```bash
# 记录失败原因
python3 failure_tracking.py record <job_id> <stage> <reason> [--resume A-AI应用] [--note "..."]
# 例：python3 failure_tracking.py record 123 resume_sent resume_rejected --resume B-解决方案

# 统计
python3 failure_tracking.py stats [--resume B-解决方案] [--days 7]

# 阶段漏斗
python3 failure_tracking.py funnel [--resume A-AI应用]

# 三线实验汇总
python3 failure_tracking.py experiment [--start 2026-08-14] [--end 2026-08-20]
```

## 实验漏斗读取方式

`get_resume_experiment_summary` 返回 A/B/C 三版的：

- applications / viewed / contacted / replied / resume_sent / interview_1 / interview_2 / offer
- view_rate / reply_rate / interview_rate / offer_rate
- top_failure_reasons（失败原因 TOP3）

注意：`contacted` 和 `resume_sent` 目前为 0（applications 表无独立字段），待 S4 状态机落地后补齐。实验重点不是「回复率」，而是「投递→查看→联系→回复→简历→一面→二面→Offer」完整漏斗。

## 兼容性

- 复用现有 `ab_experiment.db`，不破坏 applications 表（5387 条旧数据不受影响）
- 不影响 ab_test_track / job-notify / kill switch / circuit breaker / cron
- 未修改投递主流程（boss_apply.py / shared.py / deep_filter.py / hr_auto_reply.py / follow_up.py 均未改动）

## 测试

```bash
PYTHONPATH="" /usr/bin/python3 test_failure_tracking.py
# 9 项测试：正常记录 / 非法reason拒绝 / 非法stage拒绝 / 幂等去重 / resume_version统计 / 三线分别统计 / 日期筛选 / 旧数据读取 / 枚举完整性
```
