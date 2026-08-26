#!/usr/bin/env python3
"""风控阶梯降速 Risk Slowdown（Execution v2.1 任务二）——纯函数状态机，离线可单测。

把"正常 → 全停"之间加中间档（只作用于异常路径，正常投递节奏一行不动）：
  - 连续 uncertain（发送未验证）：第一次出现 → 下一次投递前间隔 ×uncertain_slowdown_factor
  - 下一次出现 verified 成功(applied) → 恢复正常间隔、连续计数清零
  - 连续 max_consecutive_uncertain 次 uncertain → 本轮提前收工（写 .paused，不动 kill switch）
  - 本轮尝试 ≥ min_attempts 次且失败率 (failed+uncertain)/attempts > failure_rate_stop → 同上提前收工

事件序列取值（与 _execute_apply 的 verified 字段对应）：
  'applied'    已验证成功
  'uncertain'  动作已发生但未验证（UNCERTAIN）
  'failed'     明确失败（弹窗拦截/会话未打开）

计数规则：'applied' 清零连击；'failed' 不累加也不清零连击——恢复/清零只由
verified 成功触发（与任务书"下一次出现 verified 成功→计数清零"一致），
整体失败风险交给失败率规则兜底。
"""

DEFAULT_FACTOR = 2.0
DEFAULT_MAX_CONSECUTIVE_UNCERTAIN = 2
DEFAULT_FAILURE_RATE_STOP = 0.30
DEFAULT_MIN_ATTEMPTS = 10


def evaluate(events, factor=DEFAULT_FACTOR,
             max_consecutive_uncertain=DEFAULT_MAX_CONSECUTIVE_UNCERTAIN,
             failure_rate_stop=DEFAULT_FAILURE_RATE_STOP,
             min_attempts=DEFAULT_MIN_ATTEMPTS) -> dict:
    """输入本轮事件序列，输出下一步动作。

    返回 {
        "stop": bool,                       # True=本轮应提前收工（调用方写 .paused）
        "reason": str,                      # 收工原因（stop=False 时为空串）
        "next_interval_multiplier": float,  # 下一次投递前的等待倍率（1.0=正常节奏）
        "consecutive_uncertain": int,       # 当前连击数（诊断用）
    }
    """
    events = [e for e in (events or []) if e in ("applied", "uncertain", "failed")]
    reasons = []

    consec = 0
    for ev in events:
        if ev == "applied":
            consec = 0
        elif ev == "uncertain":
            consec += 1

    max_consec = int(max_consecutive_uncertain)
    if max_consec > 0 and consec >= max_consec:
        reasons.append(f"连续 {consec} 次发送未验证(UNCERTAIN)")

    attempts = len(events)
    bad = sum(1 for e in events if e in ("uncertain", "failed"))
    rate = (bad / attempts) if attempts else 0.0
    min_att = int(min_attempts)
    if min_att > 0 and attempts >= min_att and rate > float(failure_rate_stop):
        reasons.append(f"{attempts} 次尝试失败率 {rate:.0%} > {float(failure_rate_stop):.0%}")

    stop = bool(reasons)
    last = events[-1] if events else None
    multiplier = float(factor) if (last == "uncertain" and not stop) else 1.0
    return {
        "stop": stop,
        "reason": "；".join(reasons),
        "next_interval_multiplier": multiplier,
        "consecutive_uncertain": consec,
    }
