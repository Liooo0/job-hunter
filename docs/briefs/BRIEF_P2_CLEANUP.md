# BRIEF — P2 工程健壮性清理（第二批，评分引擎之后的接力任务）

> ⚡ 开工须知：
> 1. **测试跑法已验证，别再探测**：回归 `PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 tests/regression.py`（当前 19/19）；test_p0 用 `cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_p0.py`（当前 13/13）；新引擎测试 `PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t .`（当前 19 OK）。你的时间花在改代码上。
> 2. **收到任务书后立即从 T1 开始动手**，不要先做长时间探索。
> 3. 每完成一个 T 就 commit——就算中途被截断，已完成的 T 也都落在磁盘和 git 里。

你是替 lio 干活的执行者。他不是程序员，只看最终效果；全程自主完成，不要中途提问。所有工作必须在本次会话内完成并自证。
前提：`match_engine.py` 统一评分引擎已合入（第一批任务）。你开工前先 `git log --oneline -3` 确认它已在。

## 一、硬性约束（违反=返工）

1. Python 3.9 兼容（系统 /usr/bin/python3），零第三方新依赖（report.py 若用 string.Template 属标准库，允许）。
2. 所有 python 命令带前缀：`PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 ...`
3. **数据红线**：ab_experiment.db 只读（report/store 的既有读路径照常），禁止任何写入/schema 变更；.kill_switch/.paused/.recovery_until/*-log.json 不碰。
4. 不碰投递主流程风控逻辑（熔断/限速/DAILY_LIMIT/暂停锁）。boss_apply.py 本批只允许删死代码和改信号处理两处。
5. 不 pip install、不联网、不改 config.json（config.example.json 是分享模板，可以改）。
6. 每完成一个任务项就单独 commit（中文 message，风格对齐历史），方便回滚。

## 二、任务清单（按序做，每项一 commit）

### T1 — report.py 模板渲染加固（原 OPTIMIZATION_PLAN B4）
- 现状：HTML_TEMPLATE 写 Jinja2 风格占位但靠 `html.replace()` 精确字符串替换渲染，缩进一动就静默失效；动态字段（公司名/岗位名/薪资）未转义。
- 改法：保持模板视觉输出完全不变的前提下，改为可靠的渲染方式（string.Template 或分段函数拼装均可），所有动态文本字段过 `html.escape()`。
- 自证：跑一次报告生成（读 store 真实数据或构造内存假数据调 generate_html 均可），确认生成的 HTML 里公司名含 `<>&"` 时页面不破（构造一条假数据自测）；现有 tests 全过。

### T2 — SIGINT 不再写暂停锁（原 A6）
- 现状：SIGHUP/SIGTERM/SIGINT 都进 `_signal_handler` → `pause()` 写 `.paused`，Ctrl+C 一下整个自动化停摆到手动 resume。
- 改法：SIGINT → 只置 SHOULD_STOP 优雅退出（打印"手动中断，本轮结束"，不写锁）；SIGHUP/SIGTERM 保持现状写锁。pause()/resume() 函数本身保留。
- 自证：grep 展示新分支逻辑 + 一个最小单测或在注释里说明验证方式（不能真起投递进程测试，写明这一点）。

### T3 — hr_auto_reply.py 修复已归档模块引用
- 现状：L245 附近 `from boss_full import scan_messages`——boss_full.py 已在 archive/legacy/ 下，运行到这条路径直接 ModuleNotFoundError。
- 改法：读上下文搞清 scan_messages 被用来干什么，优先把该函数依赖的扫描逻辑内联成独立函数（参考 archive/legacy/boss_full.py 的实现），或加清晰的降级提示（"此功能需恢复 archive 模块"）而不是裸崩。选择破坏性最小的方案并在报告里说明取舍。
- 自证：`PYTHONPATH="" ... python3 -c "import hr_auto_reply"` 不报错 + 相关函数冒烟调用不崩（不需要真连 Chrome，mock 或走到异常分支即可）。

### T4 — config.example.json 补全为当前真实结构
- 现状：只有 8 个基础字段，缺 salary_filter / body_exclude_keywords / job_pools / city_pools / safety。新人 clone 后不知道支持哪些配置。
- 改法：对照 config.json 的完整结构补齐示例值（**示例词表给少量代表性词条即可，不要整份照抄 100+ 词**；不含任何本机绝对路径和个人信息）。每个顶层块加一行 JSON 不支持注释——JSON 没有注释，就用 "_说明" 键。
- 自证：`python3 -m json.tool config.example.json` 通过。

### T5 — ab_test_track.py 词表统一（原 A4）
- 现状：POOL_KEYWORDS/CITY_PRIORITY 硬编码，与 config.json 的 job_pools.keywords/city_pools.city_priority 已经漂移（实验分组与实际投递词表对不上，实验结论失真）。
- 改法：启动时从 config.json 读 job_pools 与 city_pools 作为唯一事实源；保留旧硬编码作为兜底默认（config 缺字段时用）。注意 applications 表里历史 category 名称要能继续匹配上——映射逻辑写在注释里。
- 自证：跑 `PYTHONPATH="" ... python3 ab_test_track.py --help` 或等价只读入口不崩 + 打印加载到的分组词数。

### T6 — 死代码清理（原 B5/B6）
- 删：boss_apply.py MAX_RETRIES（定义未用）、shared.py get_today_new（全项目无调用）、boss_apply.py 里未使用的 hour_now 计算（先确认真没用）、deep_filter.py `_fetch_company_jobs`（NotImplementedError 死代码）。
- CITY_CODES 双份漂移（deep_filter.py vs boss_apply.py）：统一放 shared.py，两个文件从 shared 导入。**这步如果发现两边城市集不一致导致行为差异，保守处理：取并集写进 shared.py 并在报告里列出差异城市。**
- 自证：全部回归测试 + 单测重跑通过；`python3 -m py_compile` 每个改动文件。

## 三、最终验收（逐条贴真实输出）

```bash
cd ~/projects/job-hunter
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 tests/regression.py        # 19/19
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v   # 含 test_match_engine 在内全过
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 match_engine.py "AI应用工程师" --desc "RAG知识库与Agent工作流，Python/FastAPI" --salary "15-25K" --city 深圳   # 引擎CLI仍正常
git log --oneline -8    # 每个T一条commit
git status --short      # 干净（运行态文件除外）
ls -la ab_experiment.db # mtime 未变（证明没碰数据）
```

## 四、报告要求（中文）

1. 每个 T 的处理方式和取舍
2. 用户验证命令 2-3 条
3. 验收清单真实输出
4. 没做完的/已知限制如实写
