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
    'R1789185756-0': (
        '您好，目前在深圳看 AI 应用工程师方向的机会。2025 年本科毕业（工商管理），'
        '移动通信 + 管理的复合背景。做过的落地项目：BOSS 直聘助手 Chrome 扩展（AI 生成招呼语 + '
        '智能回复 HR 消息）、95分球鞋监控（App 签名逆向 + 视觉 LLM 精筛）、多平台求职自动化'
        '（DrissionPage CDP 驱动，累计处理 3 万+ 条岗位数据）、装修获客 AI 客服（知识库 / RAG 问答 + '
        '意向识别，已部署运行）。技术主要是 Python、LLM API 集成、Prompt 工程、浏览器自动化。'
        '简历我整理一份发您，方便的话可以聊聊具体岗位？'
    ),
    'R1789185756-1': (
        '你好，可以接受长期线上远程办公。我目前在深圳，求职方向是 AI 应用工程师：做过 BOSS 直聘助手 '
        'Chrome 扩展、Python 球鞋监控（App 签名逆向 + 视觉 LLM 两级过滤）、DrissionPage 求职自动化，'
        '以及已部署运行的装修获客 AI 客服（知识库 / RAG 问答 + 客户意向识别）。'
        '技术栈是 Python、LLM API 集成、Prompt 工程、浏览器自动化，远程协作完全没问题。'
        '方便的话可以看下我的简历再细聊？'
    ),
    'R1789185908-0': (
        '您好，我目前在深圳，正在找 AI 应用工程师方向的工作。2025 年本科毕业（工商管理），'
        '但一直自己在做技术项目：Python 做过 95分球鞋监控（App 签名逆向 + 视觉 LLM 两级过滤）、'
        '多平台求职自动投递工具（DrissionPage CDP 驱动，3 万+ 条岗位数据处理），'
        '也做过装修获客 AI 客服（知识库 / RAG 问答 + 意向识别，已部署运行）。'
        '平时主要用 Python 和 LLM API 做集成开发。方便的话能否发下具体岗位和 base 地点？有兴趣可以细聊～'
    ),
    'R1789380064-0': (
        '您好，同意发附件简历。我简单说下背景：2025 届本科毕业（工商管理），方向是 AI 应用工程师，'
        '目前在深圳。做过 LLM API 集成、Prompt 工程和浏览器自动化相关的项目，比如 BOSS 直聘助手 '
        'Chrome 扩展、95分球鞋监控（视觉 LLM 两级过滤）、多平台求职自动化投递（3 万+ 条岗位数据）、'
        '装修获客 AI 客服（知识库 / RAG 问答）。方便的话麻烦同步下岗位具体方向和上班地点，'
        '我好确认下匹配度。'
    ),
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
