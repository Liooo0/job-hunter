# PUBLIC_RELEASE_CHECKLIST — 公开仓库发布前标准检查流程

> **版本**：v2.0 (2026-09-24 固化)  
> **适用范围**：所有向 GitHub 公开仓库执行 `git push` 前的必检流程  
> **门禁工具**：`public_repo_guard v2`

---

## 核心原则

> ⚠️ **`exit 0` 不等于“自动 Push”！**  
> 门禁通过仅代表自动化扫描器未检出已知规则的违规项，最终 Push 操作必须由人工审核后显式执行。

---

## 一、标准发布七步流水线

```text
  [1. 本地代码开发与测试]
            ↓
  [2. git add 暂存变更]
            ↓
  [3. 运行发布门禁] ───────────────→ public_repo_guard --mode=release
            ↓
       【退出码裁决】
      ┌─────┼──────────────┐
      │     │              │
   exit 0 exit 2        exit 1
      │     │              │
      │   【人工核验】    【严禁 Push】
      │   (确认无误)      (立即排查修复)
      │     │
      └───→ ↓
  [4. 人工目视核对 git diff --cached]
            ↓
  [5. 确认提交身份 (Liooo0 / users.noreply)]
            ↓
  [6. 执行本地 Commit]
            ↓
  [7. 人工确认执行 git push]
```

---

## 二、门禁退出码契约 (Exit Code Contract)

| 门禁退出码 | 判定状态 (Verdict) | 业务动作 | 处理指导 |
| :---: | :---: | :---: | :--- |
| **`exit 0`** | **PASS** / **PASS WITH NOTES** | **允许进入人工 Push 流程** | 门禁全绿或仅有轻微提示（如已合并的远端分支或 PR 计数），可继续人工审核。 |
| **`exit 1`** | **FAIL** | **严格禁止 Push** | 检出高危违规（未脱敏手机号、身份证、真实姓名、API 密钥、私钥、带密码连接串、图片 GPS 经纬度、受限数据库/日志文件）。**必须清退修复**。 |
| **`exit 2`** | **REVIEW REQUIRED** | **必须人工审核确认** | 检出中危关注项（如公开摄影作品残留相机硬件序列号、历史作者暴露开发机本地主机名）。确认属预期或无害后方可决定。 |

---

## 三、Push 前最低合规要求 (Seven Mandatory Checks)

在每次公开推送前，必须确认以下七项全部达标：
1. **当前工作树扫描通过**：无未跟踪的高危文件（如 `.env`, `.db`, `.log`, `*.bak`）。
2. **Staged Diff 扫描通过**：本次将要提交的新增行中无任何硬编码敏感信息。
3. **Git History 历史全量通过**：无历史提交中“添加后删除”的敏感内容残留。
4. **Secret 扫描通过**：Gitleaks 与规则引擎密钥检出均为 0 leaks。
5. **Author / Committer 身份通过**：提交者姓名与邮箱符合公开规范（统一为 `Liooo0 <<GITHUB_NOREPLY_EMAIL>>`）。
6. **多媒体 Metadata 审计通过**：图片与视频中 GPS 经纬度为 0，相机/镜头硬件序列号已清理。
7. **无未确认的 FAIL / 阻塞项**：门禁退出码严格为 `0`（或人工授权确认的 `2`）。

---

## 四、最小操作示例 (Quick Start)

### 1. 发布门禁全检
在仓库根目录执行：
```bash
public_repo_guard --mode=release
echo "Gate Exit Code: $?"
```

### 2. 仅检查当前暂存区（推荐作为 pre-commit 快速检查）
```bash
public_repo_guard --mode=staged
```

### 3. 查看工具帮助
```bash
public_repo_guard --help
```

---

*公开仓库发布门禁标准，由 public_repo_guard v2 自动化执行并固化。*
