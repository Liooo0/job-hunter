# BRIEF — 统一岗位评分引擎 match_engine.py（P0）

> ⚡ 开工须知（2026-08-26 凌晨补，上一轮在此翻车）：
> 1. **基线已由军师验证，不要再花时间探测**：回归 19/19 通过；tests/test_p0.py 用 `cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_p0.py` 跑（13/13 通过）；`python -m unittest discover -s tests -t .` 现在可用（军师已补 tests/__init__.py）。你的全部时间应该花在写代码上。
> 2. **先写文件后跑测试**：第一轮就是只跑了基线测试就结束会话了——这是失败模式。收到任务书后 10 分钟内必须开始写 match_engine.py。
> 3. 结束会话前自检：match_engine.py / tests/test_match_engine.py / git commit 必须真实存在（ls + git log 自证），缺任何一个 = 本轮失败。

你是替 lio 干活的执行者。他不是程序员，只看最终效果；全程自主完成，不要中途提问，不要启动后台进程后提前退出。所有工作必须在本次会话内完成并自证。

## 一、项目现状（军师已核实，勿重新踩坑）

- 项目：`~/projects/job-hunter`（你就 cwd 在这里），git main 分支当前干净，HEAD=899a395。**动手前先 `git status` 确认干净。**
- 现有评分是三层串联，集成点在 `boss_apply.py` 约 L838-850：
  1. `shared.score_jd(title, desc, cfg) -> (score, reason)`：关键词分（技能命中×5 封顶30、boost词+10、排除词归零、must_contain）
  2. `shared.smart_filter(company, title, desc, salary, score, cfg, city)`：薪资底线/外包低薪/司机岗/正文排除词（大小周996夜班等）调整或归零
  3. `deep_filter.deep_filter(...)`：标题党检测/AI包装/公司背调
- **核心问题**：README 里还写着一个「五维岗位评估框架」（技术匹配30%/职业方向30%/经验15%/文化15%/地点10%，75+全力投/60-74可投/45-59斟酌/30-44跳过/<30弃）——但它只是人工模板，和上面的关键词打分不是一套引擎。本任务把它俩统一。
- **已知缺陷**：`score_jd`/`smart_filter` 用裸子串匹配 `kw.lower() in combined`，ASCII 短词会误报（"AI" 命中 "Maintained"、"Go" 命中 "Google"、"API" 命中 "Rapid"）。修法参考开源项目 BossZhiPin_Job_Search 的 job_matcher.py：ASCII 关键词首尾是字母数字时加 `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])` 词边界；纯符号侧（`.NET`/`C++` 的 `.`/`+`）不加边界；中文关键词无词边界概念，保持子串。
- **config.json 没有 `target_roles` 字段**（README 说有 27 个目标岗位，实际缺失）→ score_jd 的目标岗 +30 分支现在是死配置。引擎要从 `job_pools.keywords`（5 组词，S级-AI实施/S级-AI应用/A级车联网/A级IT支持/B级内容运营）做兜底映射，不许崩。

## 二、硬性约束（违反=返工）

1. **Python 3.9 兼容**（系统 /usr/bin/python3）：不用 3.10+ 语法（match case、`X | Y` 类型标注）。函数注解用 `typing.Optional/Tuple/Dict/List` 或字符串注解。**零第三方依赖，纯标准库。**
2. **测试环境**：Hermes 会话会注入 PYTHONPATH 污染，所有 python 命令一律带前缀：
   `PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 ...`
   （tests/ 目录无 `__init__.py`，test_p0.py 用 `cd tests && /usr/bin/python3 test_p0.py` 或 `python3 -m unittest discover -s tests -p "test_*.py" -t .` 先探测哪种能跑通，记录进报告。）
3. **数据红线**：`ab_experiment.db`（39MB 真实投递数据）绝对禁止写入/删除/重建/schema 变更。`.kill_switch` `.paused` `.recovery_until` 不碰。`*-log.json` 不碰。
4. **不碰投递主流程与风控**：boss_apply.py 的熔断/限速/DAILY_LIMIT/暂停锁逻辑一行不改。对 boss_apply.py 只允许最小侵入（见 P0-3）。
5. `~/.claude/skills/job-hunter` 是指向本项目的软链接，本项目就是唯一真身，**不要 cp -R 复制副本**（会把链接复制穿帮）。
6. 不 pip install 任何东西，不联网下载。
7. config.json 不改（target_roles 缺失由代码兜底处理）。

## 三、任务清单（按序做）

### P0-1 新建 `match_engine.py` —— 统一的可解释评分引擎

定位：**三层过滤仍是「资格层」（决定能不能投），引擎是「评估层」（把资格层信号+关键词命中明细映射成 README 五维加权总分和结论）**。LLM 不参与，纯确定性规则。

核心 API：

```python
def explain_match(title, desc, company="", salary="", city="深圳", cfg=None) -> dict
```

返回结构（字段名就用这些）：

```python
{
  "total": 87,                # 0-100 加权总分
  "verdict": "strong_apply",  # >=75 strong_apply / 60-74 apply / 45-59 consider / <45 skip
  "dimensions": {
    "technical":  {"score": 90, "weight": 0.30, "weighted": 27.0, "evidence": ["命中: Python/RAG/Dify"]},
    "direction":  {"score": 85, "weight": 0.30, "weighted": 25.5, "evidence": ["S级方向: AI应用工程师"]},
    "experience": {"score": 80, "weight": 0.15, "weighted": 12.0, "evidence": ["无高级年限门槛"]},
    "culture":    {"score": 70, "weight": 0.15, "weighted": 10.5, "evidence": ["外包公司-3分", ...]},
    "location":   {"score": 90, "weight": 0.10, "weighted": 9.0,  "evidence": ["深圳=primary城市"]},
  },
  "hits": ["Python", "RAG"],          # 技能/加分词命中
  "gaps": ["LangChain"],              # JD 提到但不在技能表的相关缺口（可选，能给就给）
  "risks": ["薪资未标注"],             # 硬性风险提示
  "keyword_score": 40,                # 原 score_jd 输出，供对照
  "notes": "..."
}
```

维度计分规则（确定性基线，可在合理范围内细化，但每条规则必须在 docstring 里写清）：

- **technical (0.30)**：cfg.skills 在 JD(标题+正文) 中命中数 n → 基线分 `min(40 + n*12, 95)`；boost_keywords 命中每个 +4 封顶 +15；正文含 Java/Spring/Android/Flutter 等传统栈且无 PYTHON_AI_SAFE 词（照抄 deep_filter.py 的 PYTHON_AI_SAFE 词表）→ 扣 20。用词边界匹配（见下）。
- **direction (0.30)**：标题命中 job_pools S级组任一词 → 90；A级组 → 70；B级 → 50；仅 boost 词命中 → 60；全不中 → 30。target_roles 若存在则优先于 job_pools（命中=95）。组间命中取最高档。
- **experience (0.15)**：标题/正文含 高级/资深/8年以上/10年以上（这些已在 exclude_keywords，正常到不了这）→ 20；含「经验不限/应届优先/1年以内/应届生」→ 85；含「3年以上/5年以上」→ 35；无明显经验表述 → 70。
- **culture (0.15)**：正文命中 body_exclude 残余风险词（大小周/996/单休/夜班——正常已被 smart_filter 拦，这里做兜底扣分而非放行）→ 每项 -30；公司名命中 KNOWN_OUTSOURCING（照抄 shared.py 名单）→ -15；正文命中 BOOST_PROJECTS（特斯拉/蔚来/智驾等高价值项目）→ +10；默认 75。
- **location (0.10)**：city == 深圳 → 90；home_cities 其余 → 80；city_pools secondary → 70；opportunistic → 55；未知城市 → 50；正文含「远程」→ 95。
- 各维度 clamp 到 [0,100]，total = Σ(weighted)，四舍五入。verdict 按 75/60/45 三条线。

同时提供：

```python
def format_report(result: dict) -> str   # 人话报告卡（中文），CLI 展示用
def load_candidate_profile(cfg) -> dict  # 从 config 归一化候选画像；target_roles 缺失时从 job_pools 兜底并在 notes 里注明
```

### P0-2 `shared.py` 加词边界匹配 + 全局替换

- 新增 `def contains_kw(haystack: str, kw: str) -> bool`：ASCII 词边界正则（缓存 compiled pattern，模块级 dict 即可），中文退化为子串。
- `score_jd` 与 `smart_filter` 里所有 `xxx.lower() in combined` / `in title_lower` / `in company_lower` 的**关键词判断**改走 contains_kw。注意逐处核对：像 `"实习" in title_lower` 这类中文词保持现状也行（contains_kw 本来就退化子串），统一替换最干净。
- **行为必须不变**：改完跑第三节的回归命令，19/19 必须全过。若有案例因词边界变严而翻转（比如某案例靠 "AI" 误命中得分才 pass），停下来分析该案例，微调词表（config 不能改就在 shared.py 内补显式词）而不是回退词边界方案。

### P0-3 `boss_apply.py` 最小接入

- 在 `run_single_cycle` 现有 deep_filter 通过之后、点击投递之前，调用 `explain_match`，把 verdict/total 追加进该岗位的日志记录与 `_record_outcome` 落库内容（先读 `_record_outcome` 和 store 的现有签名，选择破坏性最小的挂载方式：优先复用现有 reason/event 字符串拼接，或在 applications_v2/events 允许加一列文本字段——**只加列，不改不删既有列**；若加列风险大，就只在 JSON 日志条目里加 `match_verdict` 字段并在报告中体现）。
- min_score 投递判断逻辑本身不改（资格层照旧）。目标是：**每份投递的日志里以后都能查到五维结论**。
- 打印一行简版：`[🎯] 82分 strong_apply | 技术27/30 方向24/30 ...`（风格融入现有 print）。

### P0-4 CLI 入口（用户自己能玩）

`match_engine.py` 直接运行：

```bash
PYTHONPATH="" /usr/bin/python3 match_engine.py "AI应用工程师" --desc "负责RAG知识库与Agent工作流开发，Python/FastAPI" --salary "15-25K" --city 深圳
# 也支持 --desc-file jd.txt 和 --json（机器可读输出）
```

输出 format_report 的人话卡片。文件头写 3 行用法注释。

### P0-5 测试 `tests/test_match_engine.py`

覆盖至少：
1. contains_kw：("Maintained","ai")=False、("AI应用","ai") 大小写不敏感=True、("Google","go")=False、(".NET",".net")=True、("C++开发","c++")=True、("知识库","知识库")=True
2. explain_match 五维各自的关键路径（S级标题→direction≥85；传统栈无保护→technical 扣分；深圳 primary→location=90；远程→95）
3. total==Σweighted 四舍五入、verdict 边界（构造 74.x 和 75.x 两例断言两侧）
4. target_roles 缺失不崩、空 desc 不崩
5. 把 tests/regression_cases.json 全部 19 例喂给 explain_match：expect=pass 的案例 total 必须 >0 且 verdict != "skip"；expect=reject 的不做断言（资格层拦截后引擎可以任意）——这保证引擎与既有筛选口径不打架

### P0-6 README「岗位评估框架」章节更新

把「当前为手动评估模板…后续计划 RAG 改造」改为：已自动化——`match_engine.py` 每次投递自动产出五维分数+命中/缺口/风险+结论分档；保留原五维表格和阈值表（数值以本任务书为准：>=75 强烈推荐 / 60-74 可以投 / 45-59 斟酌 / <45 跳过）；补一个 CLI 用法示例。**README 其他段落（指标数字、安装说明等）一律不动。**

### P0-7 git 提交

- 只 `git add` 本次新增/修改的代码文件（match_engine.py、shared.py、boss_apply.py、tests/test_match_engine.py、README.md），**绝不 add** db/log/json 数据文件。
- 一条 commit，中文 message 风格对齐历史（如 `feat: 统一评分引擎 match_engine —— 五维加权+可解释输出，词边界修复误报，README 评估框架自动化`）。
- **commit 但不 push**（是否推 GitHub 由用户决定）。

## 四、验收自检清单（完成后逐条执行并把真实输出贴进报告）

```bash
cd ~/projects/job-hunter
# 1. 回归 19/19
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 tests/regression.py
# 2. 既有 P0 测试全过（跑法先探测，见约束2）
# 3. 新测试全过
PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v
# 4. CLI 冒烟：上面 P0-4 的示例命令，确认输出卡片包含 总分/五维/命中/结论
# 5. grep 证明词边界生效：score_jd("AI工程师","Maintained legacy systems",cfg) 的技能命中里不得出现 AI 误命中导致的假 hits（写个一次性脚本自证并贴输出）
# 6. git log -1 --stat 显示本次提交；git status 显示工作区干净（运行态文件除外）
# 7. ls -la ab_experiment.db 确认 mtime 早于你开工时间（证明没碰数据）
```

## 五、报告要求（中文，最后一条消息）

1. 问题清单：发现了什么坑、怎么处理的
2. 改动文件清单：每个文件改了什么、为什么
3. 用户怎么验证：给他 2-3 条可以直接复制运行的命令 + 他能看懂的预期输出
4. 验收清单 7 项的真实执行输出
5. 没做完的部分 / 已知限制，如实写
