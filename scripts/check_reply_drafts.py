#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核验待审核的 HR 回复草稿 —— 发出去之前过两道闸。

用法：
    env PYTHONPATH="" /usr/bin/python3 scripts/check_reply_drafts.py

闸1 数字闸（provenance.gate_outgoing_text）：话术里每个数字都要能在源事实里找到逐字佐证（数值+单位）
闸2 技术栈声明闸（provenance.check_tech_claims）：提到的技术/项目都要在源语料里有实物

源语料 = 简历 + 各项目 README（用户亲手写的 / 项目自述的），即 career-ops 说的
"Source-of-Truth Boundary"。**面试准备文档不算源事实**——那些是 AI 写的映射产物，
正是"JD 味措辞被吸收成事实"的入口。
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import provenance as pv  # noqa: E402

HOME = Path.home()
PENDING = ROOT / 'data' / 'reply_pending.json'

# 源事实：简历 + 项目自述 README（**故意不含 interview-prep/**）
CORPUS_FILES = [
    (HOME / 'projects/resume-kami/resume-E-ai-delivery.html', True),
    (HOME / 'projects/job-hunter/README.md', False),
    (HOME / 'projects/95fen-monitor/README.md', False),
    (HOME / 'projects/interview-trainer/README.md', False),
    (HOME / 'projects/jobintel-dashboard/README.md', False),
]

# GitHub 已发布仓库清单（名+描述+topics）也计入源事实。
# 2026-09-15 教训：boss-zhipin-helper 是**已发布的 Chrome 扩展**（manifest.json +
# _locales + topics chrome-extension/manifest-v3），但**本地磁盘上没有**——只看本地
# 会误判"查无实物"。反过来，Chroma/BGE 在本地和 GitHub 两处都没有 → 才是真无实物。
# 双源核验（本地 + GitHub）是这条闸的最低要求。
REPO_CACHE = ROOT / 'data' / 'github_repos.json'
REFRESH = False


def github_corpus(refresh=False):
    """返回 {'github-repos': 文本}。有缓存用缓存（离线可跑），--refresh 走网络更新。"""
    if not REFRESH and REPO_CACHE.exists():
        try:
            return {'github-repos': REPO_CACHE.read_text(encoding='utf-8')}
        except Exception:
            pass
    try:
        import urllib.request
        req = urllib.request.Request(
            'https://api.github.com/users/Liooo0/repos?per_page=100&sort=pushed',
            headers={'User-Agent': 'job-hunter-provenance'})
        with urllib.request.urlopen(req, timeout=25) as r:
            repos = json.loads(r.read().decode('utf-8'))
        lines = []
        for x in repos:
            lines.append(f"{x.get('name')} | {x.get('description') or ''} | "
                         f"{x.get('language') or ''} | {' '.join(x.get('topics') or [])}")
        txt = '\n'.join(lines)
        REPO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        REPO_CACHE.write_text(txt, encoding='utf-8')
        return {'github-repos': txt}
    except Exception as e:
        print(f"  ⚠️ GitHub 仓库清单拉取失败（{e}）——本次核验缺这一源")
        return {}


def html_to_text(s: str) -> str:
    s = re.sub(r'<style.*?</style>', ' ', s, flags=re.S)
    s = re.sub(r'<script.*?</script>', ' ', s, flags=re.S)
    s = re.sub(r'<br\s*/?>', '\n', s)
    s = re.sub(r'</(p|div|li|h[1-6])>', '\n', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()


def load_corpus():
    corpus = {}
    for p, is_html in CORPUS_FILES:
        if not p.exists():
            continue
        raw = p.read_text(encoding='utf-8', errors='ignore')
        corpus[p.parent.name if p.name.startswith('README') else p.stem] = (
            html_to_text(raw) if is_html else raw)
    # 兜底：把其它项目 README 也纳入（只加，不减）
    for extra in sorted((HOME / 'projects').glob('*/README.md')):
        if extra in [c[0] for c in CORPUS_FILES]:
            continue
        txt = extra.read_text(encoding='utf-8', errors='ignore')
        corpus.setdefault(extra.parent.name, txt)
    corpus.update(github_corpus(REFRESH))
    return corpus


def main():
    global REFRESH
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='重新拉 GitHub 仓库清单')
    ap.add_argument('--dump-md', metavar='PATH', help='把全部草稿+核验结论写成 markdown 供人审')
    args = ap.parse_args()
    REFRESH = args.refresh

    corpus = load_corpus()
    print(f"源事实语料：{len(corpus)} 份（{', '.join(sorted(corpus)[:6])}…）")
    print("⚠️ 故意不含 interview-prep/ —— 那是 AI 写的映射产物，不是源事实\n")

    queue = json.loads(PENDING.read_text(encoding='utf-8'))
    print(f"待审核草稿：{len(queue)} 条\n")

    verdicts = {'ok': [], 'fix': []}
    rows = []
    for item in queue:
        rid = item.get('id', '?')
        company = (item.get('company') or '')[:22]
        draft = item.get('draft') or ''

        num = pv.gate_outgoing_text(draft, corpus)
        tech = pv.check_tech_claims(draft, corpus)

        problems = []
        if num['blocked']:
            problems.append(f"数字无佐证: {num['blocked']}")
        if tech['unsupported']:
            problems.append(f"技术声明无实物: {tech['unsupported']}")

        tag = '❌ 需修改' if problems else '✅ 可发'
        verdicts['fix' if problems else 'ok'].append(rid)
        rows.append({'id': rid, 'company': item.get('company') or '',
                     'hr_name': item.get('hr_name') or '', 'job': item.get('job') or '',
                     'hr_message': item.get('hr_message') or '', 'purpose': item.get('purpose') or '',
                     'status': item.get('status') or 'pending',
                     'draft': draft, 'ok': not problems, 'problems': problems,
                     'num_ok': num['ok'], 'tech_ok': [s[0] for s in tech['supported']],
                     'checked': tech['checked']})
        print(f"{tag}  [{rid}] {company}")
        if num['ok']:
            print(f"        数字有佐证: {num['ok']}")
        if tech['supported']:
            print(f"        技术有实物: {[s[0] for s in tech['supported']]}")
        for p in problems:
            print(f"        ⚠️ {p}")
        if not problems:
            print(f"        签名核对通过（{tech['checked']} 项声明 / {len(num['ok'])} 个数字）")

    print()
    print("=" * 70)
    print(f"汇总：可发 {len(verdicts['ok'])} 条 / 需修改 {len(verdicts['fix'])} 条")
    if verdicts['fix']:
        print(f"需修改的 id：{verdicts['fix']}")
    print()
    print("⚠️ 本脚本只核验，不发送。发送必须由用户用『确认 <id>』逐条批准。")

    if args.dump_md:
        pend = [r for r in rows if r['status'] not in ('sent', 'rejected')]
        done = [r for r in rows if r['status'] in ('sent', 'rejected')]
        out = [f"# 待审核 HR 回复（{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}）",
               '', f"**待审核 {len(pend)} 条**：可发 {sum(1 for r in pend if r['ok'])} / "
               f"需修改 {sum(1 for r in pend if not r['ok'])}", '',
               "核准规则（第十二条，只有这些算确认）：`发送` `确认发送` `可以发` `发吧` `确认` "
               "`同意发送` `发出去`；「嗯/看看/可以/还行/行/没问题吧/应该可以」**一律不算**；超时不发送。", '',
               "每条的回复写法：`确认 <id>` 或 `拒绝 <id>`", '', "---", '']
        for r in pend:
            out += [f"## {'✅ 可发' if r['ok'] else '❌ 需修改'} — `{r['id']}` {r['company']}", '']
            out.append(f"- 岗位：{r['job']}　HR：{r['hr_name']}　意图：{r['purpose']}")
            if r['hr_message']:
                out += ['', f"**HR 原话：** {r['hr_message']}", '']
            out += ["**拟回复：**", '', '```', r['draft'], '```', '']
            if r['num_ok']:
                out.append(f"- 数字有佐证：{r['num_ok']}")
            if r['tech_ok']:
                out.append(f"- 技术有实物：{r['tech_ok']}")
            for p in r['problems']:
                out.append(f"- ⚠️ **{p}**")
            out += ['', '---', '']
        if done:
            out += ['## 已处理（存档，不再操作）', '']
            for r in done:
                out.append(f"- `{r['id']}` {r['company']} — {r['status']}")
            out.append('')
        Path(args.dump_md).write_text('\n'.join(out), encoding='utf-8')
        print(f"\n审核文件已写：{args.dump_md}")
        print(f"待审核 {len(pend)} 条 / 已处理存档 {len(done)} 条")


if __name__ == '__main__':
    main()
