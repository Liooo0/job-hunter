#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量探企业官网招聘页是否可达 + 是否含校招/择业期信息（curl 层，快）。

优先官网：先确定哪家官网能直连；WAF 拦的留给浏览器（9223/9224）。
输出：可达/不可达 + 命中的关键片段（校招/择业期/毕业时间/届）。
"""
import html
import re
import subprocess
import sys

# (公司, [候选官方招聘 URL])
TARGETS = [
    ("百川智能", ["https://www.baichuan-ai.com/", "https://www.baichuan-ai.com/join", "https://www.baichuan-ai.com/about"]),
    ("中国邮政", ["https://zhaopin.chinapost.com.cn/", "https://www.chinapost.com.cn/"]),
    ("中国建设银行", ["http://job.ccb.com/", "https://job.ccb.com/"]),
    ("中国工商银行", ["https://job.icbc.com.cn/", "https://www.icbc.com.cn/ICBC/人才招聘/"]),
    ("中国农业银行", ["https://career.abchina.com/", "https://www.abchina.com/cn/AboutABC/JobOpportunities/"]),
    ("中国银行", ["https://campus.boc.cn/", "https://www.boc.cn/aboutboc/bi4/"]),
    ("浦发银行", ["https://job.spdb.com.cn/", "https://www.spdb.com.cn/"]),
    ("北京农商银行", ["https://www.bjrcb.com/", "https://job.bjrcb.com/"]),
    ("广州银行", ["https://www.gzcb.com.cn/", "https://job.gzcb.com.cn/"]),
    ("广联达", ["https://www.glodon.com/careers", "https://job.glodon.com/", "https://www.glodon.com/"]),
    ("康明斯", ["https://careers.cummins.com/", "https://www.cummins.com/careers"]),
    ("利乐中国", ["https://www.tetrapak.com/careers", "https://www.tetrapak.com/zh-cn/careers"]),
    ("达能", ["https://www.danone.com.cn/careers", "https://careers.danone.com/"]),
    ("宝洁", ["https://www.pgcareers.com/", "https://www.pg.com.cn/"]),
    ("农夫山泉", ["https://www.nongfuspring.com/", "https://hr.nongfuspring.com/"]),
    ("豫园股份", ["https://www.yuyuan.com.cn/", "https://hr.yuyuan.com.cn/"]),
]

KWS = ['校园招聘', '校招', '择业期', '毕业时间', '应届', '往届', '届毕业生', '招聘']
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'


def fetch(url):
    try:
        r = subprocess.run(
            ['curl', '-sL', '--max-time', '18', '-H', f'User-Agent: {UA}',
             '-H', 'Accept-Language: zh-CN,zh;q=0.9', '-w', '\n@@HTTP:%{http_code}', url],
            capture_output=True, text=True, errors='ignore')
        out = r.stdout or ''
        code = ''
        m = re.search(r'@@HTTP:(\d+)$', out)
        if m:
            code = m.group(1)
            out = out[:m.start()]
        return code, out
    except Exception as e:
        return 'ERR', str(e)


def text_of(h):
    t = re.sub(r'<script.*?</script>', ' ', h, flags=re.S)
    t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', html.unescape(t))


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    for name, urls in TARGETS:
        if only and name not in only:
            continue
        print('=' * 76)
        print(f'■ {name}')
        best = None
        for u in urls:
            code, body = fetch(u)
            size = len(body)
            tag = '✅' if code.startswith('2') and size > 1500 else '❌'
            print(f'   {tag} HTTP {code} {size}B  {u}')
            if tag == '✅':
                txt = text_of(body)
                hits = []
                for k in KWS:
                    for m in re.finditer(r'[^。；\s]{0,40}' + re.escape(k) + r'[^。；\s]{0,40}', txt):
                        s = m.group(0).strip()
                        if s not in hits:
                            hits.append(s)
                        if len(hits) >= 6:
                            break
                    if len(hits) >= 6:
                        break
                if hits:
                    for hh in hits[:6]:
                        print('        ·', hh[:150])
                    best = (u, hits)
                else:
                    print('        (正文无校招/择业期关键词)')
                break
        if not best:
            print('   → 该家暂未拿到可读招聘页（可能要浏览器或换入口）')


if __name__ == '__main__':
    main()
