# BRIEF — P3 投递热路径性能 + 文档收尾（第三批接力）

> ⚡ 开工须知：
> 1. **测试跑法已验证**：回归 `PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 tests/regression.py`（19/19）；test_p0 用 `cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_p0.py`（13/13）；单测 discover（当前 31 OK）。别再探测环境。
> 2. 收到任务书立即动手。每个 R 单独 commit。
> 3. **凌晨保守条款**：R1/R2 改的是投递热路径，任何一步感觉拿不准（DOM 结构假设、翻页时机、与弹窗点击器的交互），就保持现状不动并在报告里写明原因——宁可少改，不可改坏。

你是替 lio 干活的执行者；全程自主完成，不要中途提问，所有工作本次会话内完成并自证。

## 一、硬性约束

1. Python 3.9 兼容、零新依赖；python 命令一律带 `PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc` 前缀、用 /usr/bin/python3。
2. **数据红线**：ab_experiment.db 只读（R3 统计允许 SELECT，禁止 INSERT/UPDATE/DDL）；.kill_switch/.paused/.recovery_until/*-log.json 不碰；config.json 不改。
3. **投递主流程保护名单（一行都不许动）**：熔断/限速/DAILY_LIMIT/暂停锁逻辑、seen_titles 与公司去重、弹窗自动点击器 `_dismiss_modals`、A7 投递验证状态机（`_chat_opened/_fill_and_send/_send_greeting_via_chat`）、点击后 sleep 节奏、kill switch 检查。R1/R2 只优化「读卡片信息」和「刷新页面」两处。
4. 改前确认 HEAD 在 98379ef 之后的工作区干净状态。

## 二、任务清单

### R1 — 合并 company/salary 两次全卡片 JS 遍历（原计划 P3）
- 现状：boss_apply.py 里查公司名和查薪资是两个几乎相同的 JS 块，各自遍历所有 `.job-card-wrap` 按标题匹配（约 L555-600 区域，自己定位）。
- 改法：合并为一次 `run_js` 返回 `{company, salary}`（找不到卡片时两个字段返回空串）。调用点同步改。行为语义不变：查不到时沿用原有的兜底/跳过分支。
- 自证：py_compile + 现有全部测试通过。

### R2 — 同页续投，去掉每份投递后的整页 reload（原计划 P4）
- 现状：每投一份就 `search_tab.get(search_url)` 重开搜索页（丢滚动位置、重新拉渲染，每份多 3-6 秒且请求频率更高=风控负面信号）。
- 改法：同页处理完当前卡片后继续下一条（维护已处理卡片标记/游标）；只在 翻页、换关键词、换城市 时才重新加载。若某步依赖「刷新后状态」，用最小 DOM 操作替代（如重新定位卡片列表容器），不许引入新的整页刷新。
- 风险控制：如果实现中发现同页续投会与懒加载/卡片重排冲突且无法稳妥处理，**允许降级为「只做 R1 + 报告说明 R2 未做的原因」**，这不算失败。
- 自证：py_compile + 全部测试 + 在报告里画出新的循环流程（文字版即可：什么时候刷新、什么时候同页继续）。

### R3 — 文档收尾（低风险，放最后）
1. `git mv BRIEF_MATCH_ENGINE.md BRIEF_P2_CLEANUP.md docs/briefs/`（目录新建），再 `git add` 本任务书改名后的副本也放进去（先 cp 本文件到 docs/briefs/BRIEF_P3_HOTPATH.md 再 mv 根目录原件）。
2. 更新 OPTIMIZATION_PLAN.md：在顶部实施记录区追加「2026-08-26 凌晨批次」小节，逐条标注已完成项：match_engine 统一评分引擎（新增能力）、B4 报告模板、A6 SIGINT、hr_auto_reply 内联扫描、A4 词表统一、B5/B6 死代码+CITY_CODES 统一；并更正「仍待做」清单——B2（--daily 接线）经核实此前批次已修好（boss_apply `_target_cities/_search_keywords` 已从 city_pools/job_pools 派生），从待做中划掉；剩余未做的如实列出（tab 断连自愈等）。
3. README「关键指标」表下方加一行口径说明：累计投递以 `ab_experiment.db`（store.py 单一事实源）为准，JSON 日志为历史快照；然后用**只读 SQL** 从 applications_v2 统计当前真实累计数，把表里三个口径数字更新为单一口径 + 统计日期。统计命令和结果贴进报告。
- 自证：git status 干净（除运行态文件）；README/OPTIMIZATION_PLAN 无本机敏感路径泄露检查（grep 掉 /Users/REDACTED 若新引入）。

## 三、最终验收（逐条贴真实输出）

```bash
cd ~/projects/job-hunter
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 tests/regression.py     # 19/19
(cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_p0.py)  # 13/13
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t .   # 31+ OK
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m py_compile boss_apply.py
git log --oneline -12 && git status --short && ls -la ab_experiment.db
```

## 四、报告要求（中文）

1. 每个 R 的处理方式/取舍（尤其 R2 是做了还是按保守条款降级了，为什么）
2. 用户验证命令 2-3 条
3. 验收清单真实输出 + R3 的统计 SQL 结果
4. 没做完的/已知限制如实写
