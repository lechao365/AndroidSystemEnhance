---
name: git-works-push
description: 收集工作树 diff → AI 生成中文 commit message → commit 并 push origin dev（收据随批入库）。
no_commit: false
stages:
  - research: "收集 diff"
  - plan: "AI 生成 commit message"
  - code: "commit + push"
  - review: "核对远端 sha"
---
# git-works-push

> **仅限 apply 设备（本地 WSL2）运行**。

自包含精简版：脚本做机械工作（diff 收集、git add/commit/push dev），AI 做语义工作（理解 diff、生成 message）。零确认：AI 生成 message 后直接调脚本，无需人工确认。
## Trigger（触发条件）
- cross-device-apply 编辑完成后（verify 收据已落盘）
- 人工单独提交 dev 改动
## Preconditions（前置条件）
- 当前分支 dev；工作树有改动（normal 模式）；收据文件 data/verify-results/ 已就位（随批入库）
- push 前置 CI 门禁默认开启（check_ci_head）；无 CI 或需跳过时显式
  `GWP_SKIP_CI_CHECK=1`（逃生门，见下）
## Human confirmation gates（人工确认门）
- 零确认
## Outputs / artifacts（输出/产物）
- origin/dev 新 commit（代码 + 收据同批）
- harness/log/git-works-push/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- push 失败（exit 2）：commit 保留，转人工处理（pull --rebase 后 --push-only）
- 无改动（exit 4）：提示无需推送
- CI 门禁误拦（check_ci_head 判定失败结论，实际无 CI/网络异常）：登记放弃或
  显式 `GWP_SKIP_CI_CHECK=1` 重试（逃生门，仅限确认无 CI 场景，不静默绕过）
---
## 六道 exit 1 门禁（normal 模式提交前依次硬性检查，任一不过即拒）
1. **分支守卫**：永不推 main/master；当前分支非 dev（含 detached HEAD）拒绝
2. **提交信息中文前缀**：首行须 `<中文type>(<scope>): <subject>`，type 词表
   = 新增/修复/重构/文档/构建/杂项；英文前缀（feat/fix 等）一律拒绝
3. **基线声明登记**：标题声明 `BL-YYYYMMDD-NN` 须已在 baseline-status.yaml 登记，
   未登记即拒（防未登记基线混入、promote 证据链断裂）
4. **未跟踪白名单**：未跟踪文件仅 `data/verify-results/`、`data/baselines/`、
   `data/known-issues/`、`harness/`、`code/`、`docs/`、`.github/`、`.githooks/`、
   `requirements.txt` 随批入库，名单外拒绝（防 git add -A 误吞运行态/本地产物）
5. **凭据扫描**：暂存区新增行命中 psk/password/secret/token/key 等赋值（非占位符）
   即拒（BL-20260624-01 wifi.conf psk 入库教训）
6. **提交面与收据绑定**：实际提交面 vs 最新收据 commit_scope 比对，不一致即拒；
   无收据且提交面含 `code/` 业务源码 → RECEIPT_MISSING 拒（须先经 /workspace-verify
   产收据；紧急人工场景可设 `LGW_ALLOW_NO_RECEIPT=1` 逃生门降级 warn）
## push 前置 CI 门禁
- 待推送 HEAD 与上一个已推送提交（origin/dev）的 GitHub Actions run 结论：
  failure/timed_out → 阻断（fail-closed）；上一已推送提交的历史失败降为告警
  （方向 3：不锁死修复推送）；cancelled 不算失败；API 抖动/404 降级告警不阻断
- 逃生门：`GWP_SKIP_CI_CHECK=1` 显式跳过（无 CI 场景；测试 fixture 默认置 1）
## 工作流
1. 收集 diff：git status --porcelain + git diff HEAD --stat
   （大 diff 降级判定：`git diff HEAD --stat | wc -l` > 50，或
    `git diff HEAD --numstat | awk '{s+=$1+$2} END{print s}'` > 5000；
     降级时逐文件取样 `git diff HEAD -- <file> | head -20`，仅用于生成 message）
   若改动涉及 `harness/skills/` 或 `.opencode/command/`：先跑
   `python3 harness/lib/check_skill_refs.py` 防悬空引用（2026-08-30 工具化），
   有 `[MISS]` 输出须先修复再进入提交，不得带悬空引用入库
2. AI 生成中文 commit message（harness/skills/git-works-push/docs/commit-message-format.md，六种 type）
3. 预览确认链路（可选）：bash harness/skills/git-works-push/git_works_push.sh --dry-run
4. 执行：bash harness/skills/git-works-push/git_works_push.sh --message-file <临时文件>
   （测试/注入 mock 登记表可加 --baseline-status <file>，默认 config/baseline-status.yaml）
5. 核对：git ls-remote origin dev == 本地 HEAD（脚本已重试 3 次，仍不等于则报错转人工）
## 退出码
0 成功 / 1 守卫失败（上述六道门禁或 CI 门禁任一不过）/ 2 push 失败（commit 保留）/
3 参数错误 / 4 无改动