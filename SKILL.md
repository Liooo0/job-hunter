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
> 2. 城市（**三平台城市码不同，别照抄**；见下方「平台差异速查」的城市列）
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
> 接入前置条件（SMS 登录、类名随机化）见 `README.md` 的「鱼泡接入前置条件」。
> 那边记的是 2026-09-20 **登录态下**复测的结果，推翻了更早一版「`/topic/` URL 已失效、
> 搜索不可控」的结论。

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
├── scam_guard.py             # 招聘诈骗防线（招转培/入职收费/垫付刷单）
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

## 招聘风控（scam_guard）

2026-09-20 新增。起因：人工核一个「远程办公 AI 训练师」岗（成都 / 10-15K /
主体是个人独资工作室，HR 上来就问「有没有个人电脑」），确认这类岗的判据
**不在标题，也不在公司名**。

### 为什么不做「公司名黑名单」

- 库里「远程 + 标注/训练师」命中 **47 条**，多数是**正经**居家标注岗
  （万声通讯、汉克时代、创焱智科、澳鹏…）—— 按组合词拦会大面积误杀；
- 「个人独资工作室」这类主体在工商侧是**批量注册**的，换名比词表更新快。
  按名字拉黑既拦不住，又会给人「已经防住了」的错觉。

### 三类硬信号（命中即 REJECT，与制度红线同级，任何薪资档都不例外）

| 类别 | 词表 | 例 |
|------|------|-----|
| 入职前收费 | `scam_guard.FEE_NOUNS` | 培训费 / 押金 / 设备费 / 软件授权费 / 服装费 / 建档费 / 中介费 |
| 垫付·刷单·走账 | `scam_guard.ILLEGAL_WORDS` | 垫付 / 刷单 / 走账 / 跑分 / 拉人头 |
| 显式招转培 | `scam_guard.TRAIN_TO_HIRE` | 培训后上岗 / 包就业 / 签培训协议 |

原因串形如 `诈骗红线:入职前收费(培训费)`，与 `制度红线:` / `文化红线:` /
`公司主体红线:` 同一命名族，直接 grep 就能对账。

> ⚠️ **否定语境必须放行**。正规 JD 常写「不收取任何费用」「培训费由公司承担」，
> 这是加分项，不能被词表反过来杀掉。判定按**分句 + 邻近窗口**，绝不跨标点 ——
> 否则「无需经验，需缴纳培训费」会被误放行。这条有专门用例锁着
> （`tests/test_scam_guard.py::TestNegation`）。

### 弱信号（只标记，**不参与裁决**）

进 `Decision.risk_flags`，供人工复核时看：

| 标签 | 触发 |
|------|------|
| `远程岗要求自备设备` | 远程/居家 + 自备电脑/个人电脑/需自备 |
| `主体形态:工作室/个人独资` | 公司名含 工作室/个人独资/经营部/服务部 |
| `远程+标注/训练师` | 远程 + 标注/训练师 |
| `话术:零基础+高薪` | 零基础/无需经验 + 高薪/月入/轻松 |

**这些组合正规岗也常见，拿它拦人会误杀**——这是刻意设计，不是漏做。

### 接入点与覆盖范围

判在 **`job_decision.evaluate_job`**：三个平台唯一的共同咽喉。

| 平台 | 正文层扫描 | 原因 |
|------|-----------|------|
| Boss | ✅ 有效 | `boss_apply.py` 传真实 desc |
| 51job | ⚠️ 只有公司名/标题层 | `platform_51job.py` 传 `desc=""` |
| 猎聘 | ⚠️ 只有公司名/标题层 | `platform_liepin.py` 传 `desc=""` |

51job / 猎聘 扫不到正文，根因与已知的「`jd_text` 全库为空」是同一个
（采集边界没落 JD 原文）。**已知问题、单独跟踪，不在本模块内修。**

### 人工复核一个可疑岗时的红线清单

**任何一条触发，立即终止，不用回来问：**

- 让你交任何钱（培训费 / 押金 / 保证金 / 设备费 / 软件授权费）
- 让你买指定品牌型号的设备，或从 TA 指定的渠道买
- 让你垫付「任务」资金
- 让你装远程控制类软件（TeamViewer / 向日葵 / AnyDesk）或非公开来源的软件
- 要身份证 / 银行卡 / 学生证作抵押
- 邀你做「刷单 / 点赞 / 走账」

**想再验一层，别去问 HR，问工商硬指标**：参保人数、注册地址是否虚拟、
有无对公账户。**参保 0 人 + 虚拟地址 = 直接放弃。**

## 平台差异速查

| 平台 | 脚本 | 投递方式 | 分页 | 城市参数 | 前置要求 |
|------|------|---------|------|---------|---------|
| Boss直聘 | `boss_apply.py` | 右侧"立即沟通" | 无限滚动 | 21 个（含「全国」） | 登录 |
| 前程无忧 51job | `platform_51job.py` | 搜索页内联投递按钮 | `&pageNum=N` | 16 个 | 登录 + 完善 51job 在线简历 |
| 猎聘 | `platform_liepin.py` | 详情页「投简历」(`A.btn-minor`) | 搜索页翻页 | 12 个 | 登录 |

> 城市码表在各脚本里，**不要照抄别的平台**。2026-09-20 实查（此前本文档写的
> 「51job 只支持北京/上海/广州/深圳/杭州」是过期信息，实际 16 个；猎聘那句
> 「搜索页自带城市切换」也没说清到底哪些能传）：
>
> | 平台 | 可用城市 |
> |------|---------|
> | Boss | 北京 上海 广州 深圳 杭州 成都 重庆 天津 南京 苏州 武汉 西安 长沙 合肥 厦门 宁波 无锡 东莞 佛山 珠海 + 全国 |
> | 51job | 北京 上海 广州 深圳 杭州 成都 重庆 天津 南京 苏州 武汉 西安 **东莞 佛山 惠州 珠海** |
> | 猎聘 | 北京 上海 广州 深圳 杭州 成都 重庆 南京 苏州 武汉 西安 **东莞 佛山** |
>
> 三平台都**没有**中山；Boss / 猎聘**没有**惠州。
> 猎聘的 URL 参数是 `&dqs=<城市码>` 而非 `&city=`（用错会返回全国异地岗）。

**51job 特别说明**：
- 部分校招岗位链接跳到 `yingjiesheng.com`（应届生求职网），脚本会**自动跳过**这类卡片
- 首屏卡片点击有时静默失败（服务端节流/Vue 懒加载），脚本已加重试 + 预热滚动，但单轮可能要多遍历几张卡片才能投满目标

**鱼泡直聘：未接入。** 无脚本。搜索入口是有的（`/topic/{城市码}/?keywords=`，
V1 的 `CITY_CODES` 仍有效），接入卡点见 `README.md` 的「鱼泡接入前置条件」。
