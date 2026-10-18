#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选清单（V5.8 半自动模式，2026-10-18）

为什么有它：全自动海投的边际收益在递减，主理人定「自动化停、改手动」。
本模块只负责**登记与呈现**：boss_apply.py --candidates-only 模式下，每个通过
资格/评分/双闸（含破冰语 provenance 双闸）的岗位生成一条候选记录；运行结束
按分数排序落盘 data/candidates/（运行数据，不进 Git），供人工一键复核点投。

设计约束：
  · 零重依赖（纯标准库），单测可直接导入；
  · 一切写盘只落 data/candidates/；
  · 记录里不出现求职者个人信息（电话/邮箱/真名一律没有）。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

CANDIDATES_DIR = Path(__file__).parent / "data" / "candidates"


def make_record(*, city, keyword, company, title, salary="", score=0,
                five_dim=None, value_score=None, value_tier=None,
                greeting="", template_id="", url="", plan_slot="", reason="") -> dict:
    """构造一条候选记录（字段全部来自已完成评分/过闸的上下文，不做二次判断）。"""
    return {
        "city": city, "keyword": keyword, "company": company, "title": title,
        "salary": salary, "score": int(score or 0),
        "five_dim": five_dim or {},
        "value_score": value_score, "value_tier": value_tier,
        "greeting": greeting, "greeting_template_id": template_id,
        "url": url, "plan_slot": plan_slot, "reason": reason,
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }


def sort_records(records: List[dict]) -> List[dict]:
    """按分数排序（关键词总分优先，五维总分次之）。"""
    def _key(r):
        total = int((r.get("five_dim") or {}).get("total") or 0)
        return (int(r.get("score") or 0), total)
    return sorted(records, key=_key, reverse=True)


def render_md(records: List[dict], session_ts: str = "") -> str:
    top = sort_records(records)
    L = ["# 候选清单（半自动模式）", "",
         f"生成于 {datetime.now().isoformat(timespec='seconds')} · 共 {len(top)} 条 · 按分数排序",
         "",
         "> 复核方式：挑一条 → 在 Boss 里投递时使用附带的破冰语（已过 provenance 双闸）。"
         "本清单只登记，不代替点击。", ""]
    for i, r in enumerate(top, 1):
        fd = ((r.get("five_dim") or {}).get("dimensions") or {})

        def _w(name):
            v = fd.get(name) or {}
            return v.get("weighted", "?")

        L.append(f"## {i}. [{r.get('score')}分] {r.get('company')} — {r.get('title')}")
        L.append(f"- 五维: 总 {((r.get('five_dim') or {}).get('total', '?'))}"
                 f"（{(r.get('five_dim') or {}).get('verdict', '')}）"
                 f"｜技术 {_w('technical')}/30 方向 {_w('direction')}/30 "
                 f"经验 {_w('experience')}/15 文化 {_w('culture')}/15 地点 {_w('location')}/10")
        L.append(f"- 城市/关键词: {r.get('city')} × {r.get('keyword')}"
                 + (f"｜档 {r.get('plan_slot')}" if r.get("plan_slot") else ""))
        L.append(f"- 薪资: {r.get('salary') or '未标'}"
                 + (f"｜价值 {r.get('value_score')}[{r.get('value_tier')}]"
                    if r.get("value_score") is not None else ""))
        if r.get("url"):
            L.append(f"- 链接: {r['url']}")
        if r.get("greeting"):
            L.append(f"- 破冰语[{r.get('greeting_template_id', '')}]（已过 provenance 双闸）:")
            for ln in str(r["greeting"]).splitlines():
                L.append(f"  > {ln}")
        L.append("")
    return "\n".join(L)


def write_session(records: List[dict], session_ts: Optional[str] = None,
                  out_dir=None):
    """把本轮候选写成 JSON + Markdown 两份（覆盖同 session 文件，幂等可重入）。

    返回 (json_path, md_path)。
    """
    out_dir = Path(out_dir) if out_dir else CANDIDATES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = session_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    top = sort_records(records)
    jp = out_dir / f"candidates_{ts}.json"
    mp = out_dir / f"candidates_{ts}.md"
    jp.write_text(json.dumps({"session": ts, "count": len(top), "records": top},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(top, ts), encoding="utf-8")
    return jp, mp
