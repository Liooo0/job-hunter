---
name: job-hunter
description: 求职自动投递助手。读取用户简历与偏好，在招聘平台批量按匹配度投递。触发词：投简历、自动投递、找工作、job-hunter、帮我投递
origin: local
---

# Job Hunter — 自动求职投递

## 交互流程（Claude 执行步骤）

### Step 0：检查配置

读取 `config.json`（与本 SKILL.md 同级目录）：

- **文件不存在 → 走 Step 1（首次引导）**
- **文件存在 → 跳到 Step 2（确认并投递）**

### Step 1：首次引导（生成 config.json）

向用户说明：「首次使用，我需要了解你的背景来做 JD 匹配。请提供：
1. 简历文件路径（.md / .txt / .pdf 任意）
2. 期望岗位方向（1-3 个，如：产品经理 / 前端工程师）
3. 要排除的岗位关键词（如：总监、架构师、P8 这类不适合你级别的）」

拿到简历路径后：

1. **读取简历文件**（Read 工具）
2. **从简历中抽取 8-15 个技能关键词** —— 包含：
   - 硬技能（编程语言、框架、工具：Python、React、Figma、SQL 等）
   - 业务领域词（电商、B端、支付、AI 等用户实际做过的方向）
   - 跳过：通用软技能（"沟通能力"、"团队协作"），公司名、学校名
3. **生成一个打招呼话术**，基于简历亮点，控制在 80 字内
4. **写入 `config.json`**：

```json
{
  "resume_path": "用户给的路径",
  "greeting": "你基于简历生成的话术",
  "skills": ["抽出的技能1", "技能2", ...],
  "target_roles": ["用户给的方向1", ...],
  "exclude_keywords": ["用户给的排除词1", ...],
  "boost_keywords": ["llm", "大模型", "agent", "rag", "gpt"],
  "min_score": 60,
  "default_count": 20
}
```

5. 展示生成的 config 摘要给用户确认，邀请修改。

### Step 2：收集本次投递信息

一次性问：

> 告诉我本次投递：
> 1. 搜索岗位名（如：AI应用工程师）
> 2. 城市（Boss 支持 9 大城市+全国；51job 支持北京/上海/广州/深圳/杭州）
> 3. 投递数量（默认 20）
> 4. 平台：Boss直聘 / 前程无忧 51job / 猎聘 / 都投

### Step 3：打开浏览器让用户登录

检测 Chrome 调试端口（**9223**）是否在运行。若未运行，提示用户用以下命令启动：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9223 \
  --user-data-dir="$HOME/job-hunter-chrome"
```

> ⚠️ 端口与 user-data-dir 必须和脚本里写的一致：`boss_apply.py` / `platform_51job.py` /
> `platform_liepin.py` 三个平台共用 **9223 + `~/job-hunter-chrome`**。
> 另起一个 `--user-data-dir` 等于另起一个没登录过的浏览器，脚本连上后会看到未登录状态。

连上后打开对应平台的登录页：

```python
from DrissionPage import ChromiumPage, ChromiumOptions
opts = ChromiumOptions().set_local_port(9223)
page = ChromiumPage(addr_or_opts=opts)
page.new_tab("https://www.zhipin.com/web/user/?ka=header-login")            # Boss
page.new_tab("https://we.51job.com/pc/search?keyword=AI%E5%BA%94%E7%94%A8")  # 51job
page.new_tab("https://www.liepin.com/zhaopin/?key=AI%E5%BA%94%E7%94%A8")     # 猎聘
```

51job / 猎聘没有写死的登录页 URL —— 打开上面的搜索页，未登录会自动弹登录框；
两个脚本都是靠「当前 URL 里是否含 `login`」判断登录态的（`platform_51job.py:295`、
`platform_liepin.py:170`），所以登录完跳回搜索页才算数。

提示用户：「登录页已打开，完成登录后告诉我」。

### Step 4：运行投递脚本

```bash
cd ~/.claude/skills/job-hunter

# Boss直聘（--cities/--jobs 逗号分隔）
PYTHONPATH="" python3 boss_apply.py --cities "深圳,广州" --jobs "AI应用工程师,AI实施" --count 20

# 前程无忧 51job（需要先在 51job 完善在线简历）
PYTHONPATH="" python3 platform_51job.py --cities "深圳,杭州" --jobs "AI应用工程师" --count 10

# 猎聘（外企/中高端岗更多）
PYTHONPATH="" python3 platform_liepin.py --cities "深圳" --jobs "AI应用工程师" --count 10
```

三个脚本的 CLI 形状一致（`--cities` / `--jobs` / `--count`）。

> **鱼泡直聘没有对应脚本。** 此前这里写的 `python3 yupao_apply.py …` 指向一个不存在的文件 ——
> 接入前置条件（SMS 登录、类名随机化、搜索框不受控）见 `README.md` 的「鱼泡接入前置条件」。

### Step 5：反馈结果

脚本结束后从 SQLite（`ab_experiment.db` 的 `applications_v2`）读结果给报告：
成功 / 跳过 / 失败数量，以及每条的 `reason`（跳过原因）。
旧的 `*-log.json` 只在 `store.migrate_legacy_logs()` 里作只读历史导入，不再由脚本写。

---

## 技术栈

- **DrissionPage 4.x** — CDP 连接本地 Chrome（投递浏览器端口 **9223**）
- **shared.py** — 公共模块（`load_config` / `load_log` / `save_log` / `score_jd` / `smart_filter` / `get_chrome_opts`）
- **job_decision.py** — 决策器（L2）：`evaluate_job()` 输出 `Decision(action, priority, salary_band, reason, …)`，双线 `JH_LINE=ai|transition`
- **store.py** — 单一事实源：`record_application()` 落 SQLite `ab_experiment.db`（表 `applications_v2` + `events`），旧 `*-log.json` 由 `migrate_legacy_logs()` 只读导入

## 文件结构

```
~/.claude/skills/job-hunter/
├── SKILL.md
├── README.md
├── shared.py                 # 公共：配置、评分、过滤、Chrome 选项
├── job_decision.py           # L2 决策器（双线：ai / transition）
├── deep_filter.py            # 标题党与公司背调（判拦下与否用 is_filtered()）
├── store.py                  # SQLite 单一事实源
├── match_engine.py           # 五维评分引擎（零依赖，可单独跑）
├── boss_apply.py             # Boss直聘
├── platform_51job.py         # 前程无忧 51job
├── platform_liepin.py        # 猎聘
├── config.example.json       # 配置模板
├── config.json               # 用户配置（gitignore，首次引导生成）
└── ab_experiment.db          # 投递库（gitignore）
```

> 没有 `yupao_apply.py`，也没有 `51job_apply.py` / `resume.md`。
> 鱼泡的 V1 遗留脚本在 `archive/platforms/yupao_apply.py`，**已失效、勿用**（见 README）。
> 简历路径由 `config.json` 的 `resume_path` 指定，不是固定的 `resume.md`。

## 评分逻辑（shared.score_jd）

| 条件 | 加分 |
|------|------|
| `must_contain` 非空且一个都没命中 | 归零跳过 |
| 命中 `exclude_keywords` | 归零跳过 |
| 标题含 `target_roles` 之一 | +30 |
| JD 含实习/校招/应届 | +30 |
| JD 命中 `skills` 技能词 | 每个 +5，封顶 +30 |
| JD 命中 `boost_keywords` | +10 |

≥ `min_score` 投递，< `min_score` 跳过。用户改 `config.json` 可调权重。

> ⚠️ **`exclude_keywords` 里不许放届别词（应届 / 25届 / 26届 …）。**
> `score_jd` 跑在 `job_decision.evaluate_job` **之前**，放进去等于让词表反过来否决决策器：
> 「应届生」是 `job_decision.COHORT_ALLOW_WORDS` 里明确的**放行**词，而「应届」是它的子串，
> 会把「应届生可投」这类岗位先杀掉。届别闸只有 `job_decision.cohort_block_reason` 一处实现。
> 内置兜底词表在 2026-09-20 已清掉这条（`shared.FALLBACK_CONFIG`）。

## 平台差异速查

| 平台 | 脚本 | 投递方式 | 分页 | 城市参数 | 前置要求 |
|------|------|---------|------|---------|---------|
| Boss直聘 | `boss_apply.py` | 右侧"立即沟通" | 无限滚动 | 9 大城市+全国 | 登录 |
| 前程无忧 51job | `platform_51job.py` | 搜索页内联投递按钮 | `&pageNum=N` | 北京/上海/广州/深圳/杭州 | 登录 + 完善 51job 在线简历 |
| 猎聘 | `platform_liepin.py` | "聊一聊" | 搜索页翻页 | 搜索页自带城市切换 | 登录 |

**51job 特别说明**：
- 部分校招岗位链接跳到 `yingjiesheng.com`（应届生求职网），脚本会**自动跳过**这类卡片
- 首屏卡片点击有时静默失败（服务端节流/Vue 懒加载），脚本已加重试 + 预热滚动，但单轮可能要多遍历几张卡片才能投满目标

**鱼泡直聘：未接入。** 无脚本、无可用的 URL 方案，接入卡点见 `README.md` 的「鱼泡接入前置条件」。
