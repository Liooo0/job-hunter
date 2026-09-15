# SECURITY & DATA POLICY — 公开代码仓库 + 私有运行数据

本文件是本仓库的**不可违反条款**。来源：`job-hunter` 2026-09-15 个人信息泄露事故复盘
（真实 HR 对话、本人真名、本机路径被 git 追踪并推送到公开仓库，**持续 34 天无人发现**）。

> 核心认知：**`.gitignore` 写的是「意图」，Git 记录的是「事实」。**
> 二者可以差得很远，而人会相信前者（因为它写得那么完整）。
> 所以本仓库的验收标准不是「.gitignore 写了什么」，而是「git 到底记录了/提交了什么」。

---

## 一、十二条不可违反规则

1. 真实业务数据永不进入 Git。
2. 真实用户/租户/候选人的资料永不进入 Git。
3. 真实账号、手机号、邮箱永不进入测试夹具。
4. Secrets 永不写入源码。
5. 本机绝对路径永不进入源码（macOS/Linux 的家目录绝对路径、Windows 盘的 Users 目录，一律判违规）。
6. backup / export / database / log 永不进入 Git。
7. 开发数据库与生产数据库**物理隔离**。
8. 测试数据必须是合成数据（`TEST_USER_001` / `13800000000` 这类）。
9. `pre-commit` 必须检查 **staged 内容**（不只是文件名）。
10. CI 必须检查 **全仓库 + 全历史** 的 PII / secrets。
11. 每次重大历史清理必须用 **全新 clone** 验收。
12. 公开仓库只能包含：代码、schema、脱敏示例、文档。

## 二、仓库与运行数据分离

```
代码（进 Git、可公开）              运行数据（永不进 Git）
~/projects/<project>/              ~/var/<project>/
├── app/                           ├── data/
├── scripts/                       │   └── app.db
├── tests/                         ├── backups/
├── docs/                          ├── exports/
├── .env.example                   └── logs/
├── SECURITY.md
├── .gitignore
└── .githooks/pre-commit           secrets（单独放，权限 600）
                                   /etc/<project>/app.env
```

**为什么物理分离比 .gitignore 可靠**：`git add -A` 根本扫不到仓库外的目录。
`.gitignore` 靠人记得维护，物理分离不靠人。

## 三、四层防线（L1→L4）

```
L1 文件级  路径类别直接判违规（数据库/备份/日志/导出/凭据/运行数据目录/本机服务定义）
L2 内容级  PII / 密钥 / 本机绝对路径（staged diff 的新增行）
L3 历史级  全 refs / 全历史 blob（HEAD 干净 ≠ 历史干净）
L4 流程级  pre-commit(UX) → CI(边界) → branch protection(强制) → 发布审计
```

**架构约束：任何调用方不得自写正则。** 所有模式（staged / worktree / tree / refs /
history）只调用 `scripts/public_repo_guard.py` 里同一个 `evaluate()`。
（实测教训：上一版 `--history` 分支自带一套正则，导致合成值白名单在其中失效，
历史审计永远失败——判定逻辑一旦分叉，两边会各自漂移。）

**检测规则可以公开，检测目标必须私有。** 真实姓名/称呼等具体词存在仓库外的
`~/.hermes/pii-denylist.txt`；本仓库的脚本本身可安全公开。
（反例：为了检测真名而把真名写进检测脚本 → 检测脚本自己成为泄露源。）

**`.guardrc.json`：仓库级声明。** 例外（declared_public / extra_deny_paths /
extra_allow_content）必须显式写在声明文件里并说明理由，可被 review——不要改引擎。

### 三道闸门（缺一不可）

### 闸门 1：`.githooks/pre-commit`（提交前，本地）
随仓库走（`git config core.hooksPath .githooks`），克隆即获得。
- **路径门**：数据库 / 备份 / 日志 / 导出 / .env / 私钥 / 运行数据目录 → 直接拒绝提交
- **内容门**：对 staged diff 的**新增行**扫 PII 与密钥 → 命中即拒绝
- 真实姓名等无法用正则概括的词，从**仓库外**的词表读（`~/.hermes/pii-denylist.txt`），
  这样闸门脚本本身可公开、不含任何 PII

### 闸门 2：CI（推送后，远端）
`.github/workflows/security.yml` 在 push / PR 时跑全量与全历史扫描。
个人项目也值得这么做——CI 是唯一不依赖「本机配好了」的检查。

### 闸门 3：发布前审计（人工触发）
```bash
PYTHONPATH="" python3 scripts/public_repo_guard.py --all       # 全部被追踪文件
PYTHONPATH="" python3 scripts/public_repo_guard.py --history   # 全历史
```
**公开仓库、发版、投递作品集之前必跑。**

## 四、「删除文件」不等于「删除泄露」

必须区分四个层次，它们各不相同：

```
工作区干净  ≠  Git HEAD 干净  ≠  Git history 干净  ≠  remote refs 干净
```

事故实测：`sent_replies/` 从工作区删掉后仍留在历史里；第一次清理只强推了 `main`，
**远程 tag 仍指向旧历史**，全新 clone 下来依然有 176 个含真名的 blob。

**唯一可信的验收标准**：

```bash
rm -rf /tmp/v && git clone <url> /tmp/v && cd /tmp/v
git rev-list --all | xargs git grep -lE "<PII模式>" | wc -l   # 必须为 0
git ls-remote origin | grep refs/pull                          # 检查服务端残留 ref
```

历史清理后**必须补推所有 ref**（分支 **和 tag**），详见 skill `public-repo-pii-leak-response`。

## 五、凭据存放

```bash
/etc/<project>/app.env          # chmod 600
~/.<tool>.env                   # chmod 600
```
**不要**放项目目录里的 `.env` —— 一旦 `zip project/` 或 `git archive` 就会顺手打包出去。
仓库只放 `.env.example`，键名齐全、值全空。

## 六、测试夹具

`tests/` `fixtures/` `mock/` `examples/` `seed/` **只能是合成数据**。
「只是测试数据」不构成豁免——事故里测试夹具和代码注释残留了真实姓名与真实 HR 称呼
（历史上累计百余个含真名/称呼的 blob），也正因如此，本文件连举例都只用「某先生/某女士」。

## 七、违反时的处置

1. 提交被拦 → 按提示把数据移出仓库 / 改合成值，**不要**用 `PII_GUARD_ALLOW=1` 绕过
2. 已经提交进 HEAD → `git rm --cached` + 补 `.gitignore` + 提交
3. 已经推送 → 视为泄露事件，走 `public-repo-pii-leak-response` 的完整处置链路
   （历史重写 → 强行推所有 ref → **全新 clone 验收** → 检查 `refs/pull/N/head` 残留）
