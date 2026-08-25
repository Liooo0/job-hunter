# Job Hunter — AI 辅助的求职投递工作流

> 与其广撒网，不如精准慢投：让 AI 读你的简历，只投匹配的岗位，并记录每一步的成败。

这是一个 [Claude Code](https://claude.com/claude-code) 的 **skill**：读取简历、记住求职方向与底线，在招聘平台上完成「JD 解析 → 规则过滤 → 匹配打分 → 投递 → 失败追踪 → 数据复盘」的闭环。

**设计原则：自动化提质，不追求量。** 匹配交给确定性的规则引擎，决策权始终在你手里。

## 30 秒看懂这个仓库

| 问题 | 回答 |
|------|------|
| 这是干什么的？ | 本地跑的求职工作流：按你的简历和底线给每个 JD 打分排序，只投达标的岗位，每次投递的五维评估结论落库可复盘 |
| 它和"群发爬虫"差在哪？ | ① 打分是**纯 Python 规则引擎，不走 LLM、0 token**，同一个 JD 昨天今天分数一致，每个分数都能解释到具体命中词；② 投递节奏贴近人工、带熔断和急停开关；③ 拒绝/已读不回等失败原因结构化入库，用来反哺筛选规则 |
| 谁做决定？ | 规则层只决定"够不够格"，投不投、聊什么、接不接 offer 都是人 |
| 靠谱吗？ | 作者自用跑过 **6,100+ 真实投递、34,000+ 条规则过滤、覆盖 2,700+ 家公司**（截至 2026-08 本机 SQLite 统计；本仓库不含任何个人投递数据） |

## 工作流

```
简历 → 技能关键词抽取 → 每个 JD 命中加分 / 命中排除词归零
     → 达到分数线才投 → 五维评估报告随投递落库 → 失败原因入库
     → 数据复盘：什么岗位/城市/简历版本有效
```

- **JD 解析**：结构化解出岗位与关键词，评估依据可解释
- **资格层三层过滤**（决定能不能投）：关键词打分 → 智能/薪资过滤 → 标题党与公司背调检测
- **五维匹配报告**（决定值不值得投）：技术 30% / 职业方向 30% / 经验 15% / 文化 15% / 地点 10%，输出总分 + 结论分档 + 命中/缺口/风险解释卡
- **失败追踪**：9 个阶段 × 23 种失败原因枚举入库，支持多版简历对比实验
- **数据复盘**：全部记录进 SQLite，由 [jobintel-dashboard](https://github.com/Liooo0/jobintel-dashboard) 只读可视化

## 快速开始

### 第 0 步：不用装任何东西，先玩评分引擎

评分引擎是零依赖纯标准库，没有浏览器、没有 config.json 也能跑：

```bash
git clone https://github.com/Liooo0/job-hunter.git && cd job-hunter
python3 match_engine.py "AI应用工程师" \
  --desc "负责RAG知识库与Agent工作流开发，Python/FastAPI" \
  --salary "15-25K" --city 深圳
```

输出：

```
┌─ 岗位匹配报告 ──────────────────────────────────
│ 总分: 88/100 → 🚀 强烈推荐 (strong_apply)
├─ 五维明细 ───────────────────────────────────
│ 技术匹配 ██████████ 100 ×30% →  30.0   技能命中: RAG/Agent/知识库/工作流...
│ 职业方向 █████████░  90 ×30% →  27.0   S级方向: AI应用工程师
│ 经验匹配 ███████░░░  70 ×15% →  10.5   无明显经验表述
│ 文化匹配 ███████░░░  75 ×15% →  11.2   无文化风险/加分信号(默认75)
│ 地点通勤 █████████░  90 ×10% →   9.0   深圳=primary城市
├─ 技能命中: RAG、Agent、知识库、工作流、Python、FastAPI
└─ 备注: target_roles 缺失，已从 job_pools.keywords 兜底映射 | 关键词层得分=40
```

加 `--json` 得机器可读输出，`--desc-file jd.txt` 读长 JD。

### 完整安装（自动投递）

```bash
# 1. 放进 Claude Code skills 目录
git clone https://github.com/Liooo0/job-hunter.git ~/.claude/skills/job-hunter

# 2. 装浏览器控制依赖
pip install DrissionPage

# 3. 启动带调试端口的 Chrome 并登录招聘平台（macOS 示例）
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

# 4. （可选）生成自己的配置——不做这步也开箱即用，内置了兜底词表/薪资线/岗位池
cp config.example.json config.json
```

在 Claude Code 里说一句 `帮我投简历` 即可；也可以直接手动跑：

```bash
cd ~/.claude/skills/job-hunter
PYTHONPATH="" python3 boss_apply.py --cities "深圳,广州" --jobs "AI应用工程师,AI实施" --count 20
```

## 配置说明

`config.json` 缺失时程序用内置兜底配置运行；想要个性化，改这些键：

| 字段 | 作用 |
|------|------|
| `skills` | 你的技能词 → JD 每命中一个 +5 分，封顶 +30 |
| `target_roles` | 期望岗位 → 标题命中 +30 分（缺省时从 job_pools 兜底映射） |
| `exclude_keywords` | 排除词 → 标题命中直接归零 |
| `body_exclude_keywords` | 正文排除词 → 大小周/996/销售动作词等命中归零 |
| `boost_keywords` | 加权词 → JD 命中 +10 |
| `job_pools.keywords` | S/A/B 三级岗位方向词组 → 五维「职业方向」的分档依据 |
| `salary_filter` | 城市 × 最低月薪线（home/away 两档） |
| `city_pools.city_priority` | primary / secondary / opportunistic 三档城市 |
| `min_score` | 最低投递分数线 |
| `safety` | 日上限 / 单小时上限 / 夜间禁投窗口 / 去重天数 |

完整示例见 [`config.example.json`](config.example.json)，每个块都有一行 `_说明`。

## 岗位评估框架（已自动化）

五维加权源自 [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) 的 job-evaluation 体系，针对国内求职场景调整权重，并由 `match_engine.py` 在每次投递前自动执行：

| 维度 | 权重 | 怎么算 |
|------|:---:|--------|
| 技术匹配 | 30% | 技能词命中数（ASCII 词边界正则，"AI" 不会误命中 "Maintained"）+ 加分词；传统栈无 AI 保护词扣分 |
| 职业方向 | 30% | 标题对照 S/A/B 级岗位池分档 |
| 经验匹配 | 15% | 年限门槛词分档（应届友好 85 / 无表述 70 / 高级资深 20） |
| 文化匹配 | 15% | 外包名单 −15 · 作息风险词 −30 · 高价值项目 +10 |
| 地点通勤 | 10% | primary 城 90 / secondary 70 / opportunistic 55 / 远程 95 |

| 总分 | 结论 |
|:---:|------|
| ≥75 | 🚀 强烈推荐 |
| 60-74 | ✅ 可以投 |
| 45-59 | 🤔 斟酌 |
| <45 | 🛑 跳过 |

结论随投递记录一起写入 SQLite（reason 字段含 `五维XX分:verdict`），事后可以按维度复盘哪类岗位值得投。

## 工程细节（为什么值得看代码）

- **单一事实源**：投递/跳过/失败全部写 SQLite（`store.py`），启动自动迁移旧 JSON 日志，报告/跟进/A-B 统计同源不同口径的数字打架问题不存在
- **投递验证状态机**：点击"立即沟通"后验证会话真实打开、消息真实发出，结果分为 `APPLIED` / `UNCERTAIN`（需人工复核）/ `FAILED`，杜绝"假发送"
- **风控防护**：kill switch 全局急停、跨进程日/小时双熔断、夜间禁投、公司去重——这是被平台封号两次换来的教训清单
- **确定性优先**：所有打分/过滤都是本地规则，LLM 只用于可选的 HR 消息草拟；同一输入永远同一输出
- **回归测试**：19 个真实 JD 案例回归集 + 31 个单元测试，GitHub Actions 每次 push 跑语法检查

## Token 消耗预估

| 阶段 | Tokens |
|------|--------|
| 首次引导（读简历 + 抽技能 + 生成话术） | ~6K |
| 每次启动投递（读 config + 运行脚本 + 汇报） | ~3K |
| 本地评分匹配与五维报告 | **0**（纯 Python，不走 LLM） |

投 100 个岗位的总成本 ≈ 读简历一次（一次性）+ 3K 启动，**不随岗位数线性增长**。

## 平台支持

| 平台 | 投递方式 | 城市 | 前置要求 |
|------|---------|------|---------|
| Boss 直聘 | "立即沟通" | 全国主要城市 | 登录 |
| 鱼泡直聘 | "发送简历"优先，降级"聊一聊" | 50+ 城市 | 登录 |
| 前程无忧 51job | 搜索页内联"投递"按钮 | 北京/上海/广州/深圳/杭州 | 登录 + 完善 51job 在线简历 |

51job 说明：跳转到应届生求职网的校招岗会自动跳过；首屏 Vue 懒加载卡片偶发首次点击失败，已内置重试。

## 安全与伦理

- 脚本只在你**本地浏览器**运行，简历不上传任何第三方
- 投递动作是**真实点击**，速度贴近人工、单账号低频使用；请遵守目标平台的用户协议与当地法规
- 日志与数据库保存在本地且已被 `.gitignore` 排除，API 密钥、账号信息不入库
- 不伪造简历、不代写求职信、不帮你面试——投递之后的沟通由你自己完成

## 开源许可

MIT

## 致谢

- [DrissionPage](https://github.com/g1879/DrissionPage) — 浏览器控制，比 Selenium 轻很多
- [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) — 五维岗位评估框架的灵感来源
