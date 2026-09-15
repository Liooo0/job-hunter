#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 4 条被拒的回复草稿改写成「只保留双源可核实声明」的版本，写回审核队列。

被拒原因：原草稿写「Chroma+BGE 的 RAG 匹配引擎」——本地磁盘与 GitHub（12 个公开仓库）
双向核查都没有实物；job-hunter/match_engine.py 自述「纯确定性规则，LLM 不参与，
零第三方依赖」，无向量库。该措辞来自 AI 写的面试准备文档，属"JD 味措辞被吸收成事实"。

替换策略：改用**双源可核实的等价能力主张**——
- RAG / 知识库落地 → 装修获客 AI 客服（renovation-bot，双库分层知识库 + RAG 问答，已部署）
- 向量库/embedding 相关措辞一律不写
其余项目声明（Chrome 扩展 / 球鞋监控 / DrissionPage / LLM API / Prompt）均已核到实物，保留。
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / 'data' / 'reply_pending.json'

NEW = {
    "R1789185756-0": "在看。深圳，AI 应用方向。做过 BOSS直聘助手 Chrome 扩展、95分球鞋监控（API 逆向 + 视觉 LLM 精筛）、多平台求职自动化、装修获客 AI 客服（知识库/RAG，已上线）。主要用 Python + LLM API。简历发你？",
    "R1789185756-1": "可以，远程没问题。深圳，AI 应用方向。项目有 BOSS直聘助手 Chrome 扩展、球鞋监控（API 逆向 + 视觉 LLM）、DrissionPage 求职自动化、装修获客 AI 客服（知识库/RAG 已上线）。简历你先看下？",
    "R1789185908-0": "在考虑。深圳，AI 应用方向。项目：95分球鞋监控（API 逆向 + 视觉 LLM）、多平台求职自动投递（DrissionPage，3 万+ 条岗位数据）、装修获客 AI 客服（知识库/RAG 已上线）。主要用 Python + LLM API。方便说下岗位和 base 吗？",
    "R1789380064-0": "同意，简历发你。深圳，AI 应用方向：BOSS直聘助手 Chrome 扩展、球鞋监控（视觉 LLM 两级过滤）、多平台求职自动化（3 万+ 条岗位数据）、装修获客 AI 客服（知识库/RAG）。顺带问下岗位方向和上班地点？"
}

queue = json.loads(QUEUE.read_text(encoding='utf-8'))
bak = QUEUE.with_name(f'reply_pending.bak_{datetime.now():%Y%m%d_%H%M%S}.json')
shutil.copy2(QUEUE, bak)
print('已备份:', bak.name)

n = 0
for item in queue:
    if item.get('id') in NEW:
        item['draft_original'] = item.get('draft')
        item['draft'] = NEW[item['id']]
        item['status'] = 'pending'
        item['rewritten_at'] = datetime.now().isoformat(timespec='seconds')
        item['rewrite_note'] = '移除查无实物的 Chroma+BGE 声明，改用具实物的知识库/RAG 项目'
        item['reject_reason'] = 'Chroma/BGE 无实物（已改写重审）'
        n += 1
        print(f"  改写 {item['id']}")
tmp = QUEUE.with_suffix('.tmp')
tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding='utf-8')
tmp.replace(QUEUE)
print(f'完成：{n} 条写回 pending，等待你审核')
