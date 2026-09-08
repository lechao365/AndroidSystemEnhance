# harness 业界对齐评估与修复设计（P0 快检/lint/自度量 + P1-A 覆盖率 + P1-B 审批独立）

- 日期：2026-09-08
- 状态：待评审
- 范围：五项修复——① 业务快检进 CI + workspace-verify `--quick`；② 静态检查门禁化（ruff + clang-tidy/format）；③ harness 自度量统计工具（metrics.py）；④ 覆盖率采集闭环；⑤ promote 审批独立性（修复 KI-20260907-001）。仅改 harness 机制层 + 业务测试接线，不改业务 C/C++ 行为。

## 1. 背景与目标

### 1.1 现状问题（调查实证，对应打分维度）

| # | 维度 | 问题 | 证据 |
|---|------|------|------|
| P1 | D3/D4 反馈延迟 | 业务代码无"快检先行"层，改内核纯逻辑也要走完整同步+增量编译+上板链；GitHub CI 只检 harness 自身 pytest，业务单测零进 CI | `.github/workflows/selfcheck.yml` 仅 9 个 rc；`code/rpi5/kernel/new/vendor/lechao/*/tests/Makefile` 无 CI/顶层汇总入口 |
| P2 | D4/D6 检查左移 | 全仓无 ruff.toml/pyproject/.clang-format/.clang-tidy；ruff 有运行痕迹（.ruff_cache）但未落地为门禁 | `ls ruff.toml pyproject.toml .clang-format .clang-tidy` 全不存在 |
| P3 | D2/D5/D6 可观测 | trend.md 是纯文本 1000 行无查询/渲染；flake 率、pass 率、平均验证时长、各 skill 耗时无统计维度 | `grep dashboard/统计报表` 零命中；收据无 flake 计数 |
| P4 | D4 覆盖率 | `native_coverage: true` 插桩存在但无采集链/无收据字段/无门禁，属"插桩摆设" | `tests/Android.bp` 三处 native_coverage；verify-cases.yaml 无 coverage 项 |
| P5 | D9 审批独立 | `--approved-by` 恒等于执行人，审批自证，缺独立隔离 | `baseline_register.py:561-566` 仅判非空；已登记 `KI-20260907-001` open |

### 1.2 借鉴来源与目标

业界参照：DORA 核心门禁 <10 分钟、检查左移（ruff/clang-tidy）、Google 覆盖率度量→门禁路径、SLSA 独立审批。目标是让 harness 更符合业界思想、更方便 AI 自助使用——AI 每轮修复有廉价快检、改动即得静态反馈、可自助查健康度、覆盖率成证据、审批不可自证。

## 2. P0-A 业务快检进 CI + workspace-verify `--quick`

### 2.1 新增 CI job（GitHub 托管 runner，无真机）

在 `selfcheck.yml` 增一个 `host-tests` job（保持最小权限、SHA 固定 action、无 secrets 约束）：

- 内核 host 单测：`make -C code/rpi5/kernel/new/vendor/lechao/LcView/tests test` + 同 LcIod/tests（gcc 纯逻辑，`-Wall -Wextra -Werror`，ubuntu-latest 自带 gcc）
- ruff 检查（P0-B 落地后）：`ruff check harness/ code/`
- 新增 rc：`host_rc` 进 `REQUIRED_RC_KEYS`，**同一 rc 同时进本地 selfcheck 与 CI**（host 单测是 gcc 纯逻辑，`code/` 内可直接 `make -C ... test`，本地/runner 一致）——rc 增删须同步 `ws_report.py` 必查键、CI 解析循环、`test_workflow_ci.py` 集合断言三处，并带"破坏即判红"测试用例（CDP-DOD-001 检查器门禁三要件）

### 2.2 workspace-verify `--quick` 模式

`ws_verify_chain.py` 增加 `--quick` 开关：只执行「sync → 影响面判定 → 增量编译 AOSP 单测 targets → host 单测」，跳过 adb 推送/上板验收/收据（或写一个 `verify_mode: quick` 的轻量收据，不占 `board`）。用途：AI 编辑纯逻辑/单测后快速确认可编译、可过单测，再决定是否走完整上板链。

- 决策点：quick 是否落收据。建议**不落 board 收据**（防止被误当 board 证据登记 candidate），仅日志+退出码。落收据留二期。
- 收益：AI 修复循环从"小时级全链"降到"分钟级快检"；真机不被琐碎改动占用。

## 3. P0-B 静态检查门禁化

### 3.1 ruff（harness Python）

- 新增 `harness/config/pyproject.toml` 或仓根 `ruff.toml`（选仓根，与 CI job 同根）：line-length 100、`E/F/I` 规则集起步、`harness/` 扫描、排除 `log/` `__pycache__` `.pytest_cache`。
- `selfcheck.py` 新增 `ruff_rc`：`ruff check harness/`（当前仓已有 `.ruff_cache`，先 `ruff check --fix` 清存量再作为门禁）；`ruff_rc` 进 `REQUIRED_RC_KEYS` + 本地 selfcheck + CI 解析循环 + `test_workflow_ci.py` 集合断言（三处同源）。
- 收益：AI 改 harness Python 自动获得格式化/静态反馈；与 pytest 同链进自检与 CI。

### 3.2 C/C++（业务代码）

- 新增 `.clang-format`（Google/LLVM 风格）+ `.clang-tidy`（`clang-analyzer-*`、`bugprone-*` 保守起步）于仓根。
- 接入点（不强求 clang-tidy 全覆盖，避免噪音淹没信号）：
  - `.clang-format --dry-run --Werror` 对 `code/rpi5/kernel/new/vendor/lechao/**/*.[ch]`、`code/rpi5/aosp/new/vendor/lechao/**/*.{cpp,h}` 进 CI `host-tests` job 的 `fmt_rc`；
  - clang-tidy 只对**内核 host 单测已覆盖的纯逻辑文件**（`lcview_ring_logic.c`、`lciod_read_logic.c`）先接，验证可用后扩面——避免对依赖 AOSP/内核头的大量文件空跑。
- 收益：格式/静态约束机械化，减少人工 review 往返；符合检查左移。

## 4. P0-C harness 自度量统计工具

### 4.1 `harness/lib/metrics.py`（新增）

聚合三份既有资产，输出结构化报表：

- **来源**：`data/verify-results/*.md`（收据 20 字段）、`harness/log/workspace-verify/trend.md`（每批一行 result+timings JSON）、`data/known-issues/*.md`（flake/idle-eligible 计数）
- **输出**：`python3 harness/lib/metrics.py --report [--json]` → pass 率 / skip 率 / fail 率、平均与 P90 验证时长（按 stage）、flake 率（kind=flake / 总批）、已知问题状态板（open/fixed 计数）、按 skill 的耗时分布
- **收据 flake 计数**：`ws_report.py` 收据字段新增 `flake_count`（本批 selfcheck 登记 flake 数），供 metrics 聚合

### 4.2 约束

- 只读聚合，不写仓内文件（输出 stdout / `harness/log/metrics/` 可选）；幂等；对缺字段容错（收据老化后不崩）。
- 自检：`metrics_rc` 进 REQUIRED_RC_KEYS（跑通即 0，解析失败判红；含空目录/坏 JSON 场景），配 `harness/lib/tests/test_metrics.py`（覆盖空目录/坏 JSON/部分缺字段）。
- 收益：AI 可一条命令自查项目健康度，据此做 loop 预算/修复决策；为后续仪表盘打底。

## 5. P1-A 覆盖率采集闭环

### 5.1 采集链

- `ws_upload_tests.py`（或新 `ws_coverage.py`）增加 coverage 采集步：测试跑完后对 `native_coverage: true` 的 target 执行 llvm-cov/lcov 归并，产出每文件行覆盖报告。
- 收据新增 `coverage` 字段（JSON：target → 行覆盖率 %，含 `files` 摘要），`ws_report.py` 透传。
- 一期**只记录不门禁**（对齐 lcview-perf "只报数不设门禁"先例），二期做阈值门禁与趋势告警。
- 风险：AOSP native_coverage 产物（`.gcda`）需在设备真跑时回传——`ws_upload_tests` 已 push 二进制并跑 gtest，回传 gcda 需在现有回传通道上加目录。若设备侧 gcda 采集不可行，降级为"编译期静态覆盖（llvm-cov export from .o + -fprofile-instr-generate）"并如实标注。
- 收益：覆盖率从"插桩摆设"变证据；跨批可 diff 趋势；为金字塔度量补最后一环。

## 6. P1-B promote 审批独立性（修复 KI-20260907-001）

### 6.1 方案（推荐：执行人身份硬隔离 + 审批凭据外部化）

`baseline_register.py promote` 增加两道校验：

1. **身份不等式**：`--approved-by` 解析出的审批人身份（支持 `Name <email>` 或纯 Name）不得等于本次执行人的 git 身份（`_collect_operator` 同源采集），相等即拒并提示换独立审批人。
2. **审批凭据外部化**：新增 `LC_PROMOTE_APPROVAL_TOKEN` 环境变量（评审人独立持有，类 opencode-server 的 server.env 凭据外部化模式）；promote 校验该 token 非空、不含 `<PLACEHOLDER>` 类占位符、且与 `harness/config/promote-approval.env`（gitignore 不入库）预设值一致，任一不符即拒。

配套：`baseline-evidence-template.yaml` 与 `baseline-status.yaml` 的 `approved_by` 注释更新（说明独立隔离要求）；`check_config.py` 若校验 yaml 字段则同步。

### 6.2 备选（评审人通道，重，二期）

`HARNESS_ROLE=reviewer` 设备/角色 + 二段式 `approve` 子命令独立落审批记录。因当前单开发者环境无第二台设备，列为二期选项，设计预留 `approver_identity` 字段与 promote 校验接口即可兼容。

- 收益：修复已登记缺陷 KI-20260907-001；符合 SLSA 独立审批思想；审批环节证据链真正可信。

## 7. 影响面与回归

| 文件 | 影响 |
|------|------|
| `.github/workflows/selfcheck.yml` | 新增 host-tests job + rc 解析扩列 |
| `harness/lib/selfcheck.py` | 新增 `ruff_rc`/`metrics_rc`（及 `host_rc` 若走本地） |
| `harness/lib/tests/*` | 新增 test_metrics.py；test_workflow_ci.py 集合断言同步 |
| `harness/skills/workspace-verify/ws_verify_chain.py` / `ws_report.py` | `--quick` 开关；收据 `flake_count`/`coverage` 字段 |
| `harness/skills/publish-main-base/baseline_register.py` | promote 审批独立校验 |
| `harness/config/baseline-evidence-template.yaml` | approved_by 说明更新 |
| 仓根 `ruff.toml` / `.clang-format` / `.clang-tidy` / `requirements.txt` | 新增（requirements 增 `ruff`） |
| `code/rpi5/kernel/new/vendor/lechao/*/tests/Makefile` | 不改（CI 直接 `make -C ... test`） |

- 新增检查器均遵循 CDP-DOD-001 三要件：有调用方（selfcheck/CI）、破坏即判红用例、rc 进 REQUIRED_RC_KEYS。
- 不动业务 C/C++ 行为；不改跨设备批次契约（emit/apply 零影响）。

## 8. 验证方式

1. `python3 harness/lib/selfcheck.py`（本地）9→12 rc 全 0
2. `python3 -m pytest harness/ -x -q` 全绿（含新增 test_metrics）
3. `python3 harness/lib/metrics.py --report --json` 输出合法
4. CI 模拟：`ruff check harness/` + `make -C LcView/tests test` 在 ubuntu runner 上通过（本仓无 runner，靠 CI 实际跑一次确认）
5. `--approved-by` 等于执行人身份时 promote 拒；不同身份 + token 匹配时通过（`--check`/dry-run 验证）
