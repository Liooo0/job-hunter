#!/usr/bin/env python3
"""决策链快照 Decision Trace（Execution v2.1 任务一）。

每次投递/跳过都留下完整决策证据：六道过滤门各自的结果（含被上游短路时的
not_reached 标记），随 applications_v2.gates 列与 events payload 落库。

设计约束：
- trace 就是普通 dict（可 json.dumps），不引入任何依赖；
- 门的结果枚举固定三种：pass / rejected:<原因> / not_reached；
- 短路语义由调用方保证：被上游杀掉的岗位绝不允许触发下游门，
  下游门保持 new_trace() 预填的 not_reached，一个请求都不多发。
"""

import json

# 固定门序（与 boss_apply._prepare_job_context 执行顺序一致）：
#   dedup            同公司去重（循环体内，先于一切过滤）
#   smart_filter     智能过滤（公司规模/性质/薪资/技术含量）
#   deep_filter      深度筛选（标题党/实习陷阱）
#   company_profile  公司背调 API（唯一有网络请求的门，必须严格短路保护）
#   min_score        最低分门槛
#   already_chatted  已沟通过检查
#   apply            执行阶段（点击沟通/填发招呼语）
GATES = (
    "dedup",
    "smart_filter",
    "deep_filter",
    "company_profile",
    "min_score",
    "already_chatted",
    "apply",
)


def new_trace() -> dict:
    """新建一条 trace：所有门预填 not_reached，最终决策留空。"""
    return {
        "gates": {name: {"result": "not_reached", "detail": ""} for name in GATES},
        "final_decision": "",
        "final_reason": "",
    }


def gate(trace, name, result, detail=""):
    """记录某道门的结果。result 取值：pass / rejected:<原因> / not_reached。"""
    if trace is None:
        return
    trace.setdefault("gates", {})[name] = {
        "result": str(result),
        "detail": "" if detail is None else str(detail),
    }


def finalize(trace, decision, reason=""):
    """补上 final_decision / final_reason（在落库前调用一次）。"""
    if trace is None:
        return
    trace["final_decision"] = str(decision)
    trace["final_reason"] = "" if reason is None else str(reason)


def to_json(trace):
    """序列化供落库；无 trace 时返回 None（旧数据该列为 NULL，读取端需容忍）。"""
    if not trace:
        return None
    return json.dumps(trace, ensure_ascii=False)
