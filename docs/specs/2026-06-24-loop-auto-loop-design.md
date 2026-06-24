# Loop Engineering 全自动闭环设计

> **状态**：待用户审阅
> **日期**：2026-06-24
> **前置**：`2026-06-19-loop-engineering-v2-design.md`、`2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md`、`2026-06-22-lciod-loop-verification-design.md`、`engineering/loop/WORKFLOW.md`
> **影响**：`engineering/loop/WORKFLOW.md`（核心流程章节重写）、`engineering/loop/controller/`、`engineering/loop/deploy/`、`engineering/loop/core/python/loop_core/cli.py`

---

## 1. 背景与动机

### 1.1 原设计的妥协

`2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md` §1 明确写："首版只做候选补丁草案 + 人工确认，不自动 apply、不自动编译部署、不自动多轮循环。" 这是早期妥协，旨在先用最小风险跑通流程。

### 1.2 新目标

将核心流程升级为**全自动闭环**：从 `le control init` 起，循环执行"验证→分析→补丁→编译→部署"，**仅在 全pass（成功）或 escalate 条件触发时停止**，中间无需人工介入。

### 1.3 驱动模式确认

- **驱动者**：opencode 主会话 AI，按闭环 SOP 文档（见 §8）逐步调用 `le control` 子命令串联整个闭环。
- **Analyzer**：主会话 AI 即 Analyzer（读 evidence_bundle → 出诊断+补丁草案）。`LlmAnalyzer` ABC 仅作为"未来独立化"的接口预留，本次不实现其具体子类。
- **不引入独立 auto-loop 进程**：三份核心 spec 一致指向"主会话内调度"，且独立进程需自行集成 LLM API（鉴权/超时/成本/幻觉兜底），工作量与风险均不符 YAGNI。

---

## 2. 当前框架缺失诊断

基于对 `engineering/loop/` 全量源码与设计文档的对照分析，识别如下缺失项。

### 2.1 硬缺失（阻断全自动闭环）

| 编号 | 缺失项 | 证据 | 影响 |
|---|---|---|---|
| **A1** | `le gen-cases` 未实现（cli.py:100 占位返回 1） | WORKFLOW.md 遗留点 #1 | 步骤"AI 生成 YAML 用例"无校验入口 |
| **A2** | 闭环无"一键驱动"——control_cli 仅 6 个离散子命令，需人工逐步调；且存在 5 个硬伤（见 §2.2） | control_cli.py | 全自动串联不可行 |
| **A3** | `LlmAnalyzer` 仅 ABC，`MainSessionAnalyzer` 是 stub | analyzer_protocol.py | 分析/补丁无编程调用入口（但按驱动模式确认，由主会话 AI 承担，A3 收敛到 A2 的 SOP） |

### 2.2 control_cli 现有硬伤（G1-G5）

基于 `control_cli.py` 实际源码：

| 编号 | 问题 | 证据（行号） | 影响 |
|---|---|---|---|
| **G1** | `run-verify` 硬编码 `evidence_{N}.json`，但实际产出为 `evidence_bundle.json` | L106 | session 记录的 evidence_path 指向不存在文件 |
| **G2** | `analyze-request` 读 `evidence_{N}.json`（同 G1 错位）；且 `failed_cases` 依赖 attempts[].failed_cases，但 run-verify 从未填该字段 | L132-136, L148 | 分析请求拿不到真实失败用例 |
| **G3** | 无 `apply-patch` 子命令——patch_applier 存在但无 CLI 入口 | 全文仅 6 子命令 | 全自动无法触发补丁应用 |
| **G4** | 无 `compile` 子命令——deploy 内部直接调 deploy（含编译+部署），无独立编译+失败回滚 | L162-172 | 编译失败无法自动 revert |
| **G5** | `decide` 自行实现极简版，未调用 `cycle_orchestrator.decide_next_from_session()` / `policy.decide_termination()`；无 failure_code 记录、无补丁去重、无 REPEATED_FAILURE 检测 | L175-186 | 循环终止逻辑形同虚设 |

### 2.3 覆盖不全

| 编号 | 项 | 处理（见 §6） |
|---|---|---|
| **B1** | FLASH_FULL（sepolicy/.te/混合改动）原不自动刷机 | 升级为全自动 dd + 四阶段防护网（§7） |
| **B2** | `loop_workflows/python` 空骨架 | 不填（YAGNI，当前编排由主会话 AI 按 SOP 承担） |

### 2.4 文档缺口

| 编号 | 项 | 处理 |
|---|---|---|
| **C1** | `docs/specs/2026-06-22-loop-controller-sop.md` 在 lciod-loop §5.10 列出但未创建 | 本文档 + §8 SOP 章节共同填补 |

---

## 3. 核心决策汇总

| 决策点 | 选定 | 理由 |
|---|---|---|
| 闭环驱动者 | 主会话 AI 按 SOP 串联 control 子命令 | 三份核心 spec 一致指向；不引入独立进程 |
| Analyzer | 主会话 AI 即 Analyzer；LlmAnalyzer ABC 仅预留 | 避免独立 LLM API 集成的工作量与风险 |
| A1 生成用例 | 主会话 AI 生成 YAML；`le gen-cases --validate` 仅校验 | 与整体驱动模式一致 |
| 安全边界 | **全自动**（取消原步骤5人工确认） | 用户明确要求升级 |
| 补丁层护栏 | 白名单校验 + 语法检查 + git stash 备份 | 全自动前提下的最低防线 |
| 编译层护栏 | 失败自动 `git revert`（stash pop） + 计入 N | 全自动前提——失败必须可回滚 |
| 部署层护栏 | 能 PUSH_SINGLE 不 dd；dd 前后四阶段防护网（§7） | 最小风险 + 多层前置过滤 |
| 循环层护栏 | N=5 + failure_code 去重 + 补丁内容去重 | 防死循环 |
| dd 物理边界 | kernel 死（serial 无 shell）→ escalate 人工物理重刷 | 软件无法突破 |
| control_cli 架构 | 方案X：离散子命令补全 | 与主会话驱动模式一致 |

---

## 4. 更新后的核心流程

### 4.1 八步全自动闭环

```
[Step 0] (可选) 主会话 AI 读代码/spec + case-template.md → 生成 YAML 用例
         → le gen-cases --validate <file>   # 校验 schema/断言/命名/依赖/foreach/action

[Step 1] le control init --target <module> --max-attempts 5 --artifacts-dir <dir>
         → 创建 session.json（含 target/max_attempts/current_attempt=0/status=PENDING）

[Step 2] le control run-verify --session <sid> --suite <suite> [--adb-endpoint <ep>]
         → 执行用例 → evidence_bundle.json → 提取 failed_cases 写入 session

[Step 3] le control decide --session <sid>
         → 调用 cycle_orchestrator.decide_next_from_session()
         → PASS              → STOP（成功，闭环结束）
         → RETRY             → 继续 Step 4
         → STOP + escalate   → 人工介入

[Step 4] (仅 RETRY) le control analyze-request --session <sid>
         → 生成 analysis_request.json（failed_cases + collectors_output + workspace_diff_so_far）
         → 主会话 AI 读 evidence_bundle.json + analysis_request.json
         → 输出诊断结论 + patch.json（FileChange[] 序列化）

[Step 5] (仅 RETRY) le control apply-patch --session <sid> --patch <patch.json>
         → 护栏1: 白名单校验（仅 target 模块路径；越界→PATCH_REJECTED + 计入 N）
         → 护栏2: 语法检查（按扩展名 dispatch linter）
         → 护栏3: git stash create 备份（记录 stash_ref 到 session）
         → 内核改动风险标记（.c/Makefile/defconfig/init.rc → RISK=KERNEL，触发阶段2更严格检查）

[Step 6] (仅 RETRY) le control compile --session <sid>
         → 成功 → 继续 Step 7
         → 失败 → le control revert --session <sid> → 计入 N → goto Step 3

[Step 7] (仅 RETRY) le control deploy --session <sid> [--adb-endpoint <ep>]
         → DeployDecider 决策（能 PUSH_SINGLE 则不 dd；详见 §4.2）
         → PUSH_SINGLE: root+remount+push+restart service
         → DD_BOOT_REBOOT / FLASH_FULL: 四阶段防护网（§7）+ serial 回退
         → 部署失败 → 回退（如可）→ 计入 N → goto Step 3

[Step 8] goto Step 2，直到全 pass 或 escalate
```

### 4.2 部署模式优先级（硬约束）

```
DeployDecider 决策顺序：
1. PUSH_SINGLE（二进制 / .cpp 改动 / 脚本）  → 推送，不 dd
2. DD_BOOT_REBOOT（boot.img / vendor / .te） → 全自动 dd + 备份 + serial 回退（§7）
3. FLASH_FULL（sepolicy 大改 / 混合改动）    → 全自动 dd + 备份 + serial 回退（§7）

"能推送小包不 dd" 是 #1 优先级硬约束：只要改动可走 PUSH_SINGLE，绝不上 dd。
```

### 4.3 escalate 触发条件

| 条件 | 来源 |
|---|---|
| `current_attempt > max_attempts`（N=5） | policy.decide_termination |
| 同 `failure_code` 连续重复 | policy.decide_termination（REPEATED_FAILURE） |
| 补丁内容重复（patch_hash 相同） | 新增：cycle_orchestrator 计算 patch_hash 去重 |
| 白名单拒绝（PATCH_REJECTED） | patch_guard.py |
| 编译失败超阈值（同一补丁编译失败即计入 N，连续编译失败 escalate） | policy + failure_code=COMPILE_FAILED |
| boot_completed 超时 + serial 无 shell（kernel 死） | deployer.py + rollback.py |
| 关键 service 刷写后不存活（半砖） | deployer.py smoke |

---

## 5. control_cli 补全设计

### 5.1 修复现有缺陷（G1/G2/G5）

#### G1 修复：evidence 路径对齐

`run-verify` 改为：执行 `loop_core.cli run` 后，从 artifacts_dir 读取实际产出的 `evidence_bundle.json`（而非硬编码 `evidence_{N}.json`）。若产物不存在或读取失败，attempt 记录 `verify_result=ERROR` + `failure_code=SESSION_STATE_ERROR`。

#### G2 修复：failed_cases 提取

`run-verify` 执行成功后，读取 evidence_bundle.json，遍历 `cases[]`，将 `status in ("fail","error")` 的用例提取为 `failed_cases`（字段：id/status/failure_reason/command），写入 `session.attempts[-1].failed_cases`。

`analyze-request` 改为：直接从 session 的最近 attempt 取 failed_cases + 从 evidence_bundle_path 取 collectors_output，构造 `AnalysisRequest`。

#### G5 修复：接入真正的 policy

`decide` 改为：
1. 从 session 构造 `SessionState`（含 attempts/stage_results/failure_codes）。
2. 调用 `cycle_orchestrator.decide_next_from_session(session)`。
3. 该函数内部调 `policy.decide_termination(max_attempts, current_attempt, latest_stage, previous_failure_codes)`。
4. 输出 `decision=STOP/RETRY` + `reason_code` + `should_escalate` + `reason_summary`。

补丁内容去重：`cycle_orchestrator` 新增逻辑——计算最近 attempt 的 `patch_hash`（对 patch.json 内容取 sha256），若与之前任一 attempt 的 patch_hash 相同，视为重复补丁，强制 `decision=STOP` + `should_escalate=true`。

### 5.2 新增子命令

#### `apply-patch`

```
le control apply-patch --session <sid> --patch <patch.json>
```

职责：将 AI 生成的 FileChange[] 应用到 workspace，前置三层护栏。

| 步骤 | 动作 | 失败处理 |
|---|---|---|
| 1. 加载 patch.json | 反序列化为 `list[FileChange]` | 格式错误→返回错误 |
| 2. 白名单校验 | 逐一校验 `fc.workspace_path` 落在 `target-paths.yaml` 的 allowed_prefixes 内 | 越界→PATCH_REJECTED + 计入 N |
| 3. 语法检查 | 按扩展名 dispatch：`.c/.cpp→gcc -fsyntax-only`、`.java→javac -Xlint`（或跳过无 linter 的类型）、`.te→sepol 中间产物检查`、`.xml/.yaml→对应 parser` | 语法错→PATCH_REJECTED + 计入 N |
| 4. git stash 备份 | `git stash create -u`（含未跟踪文件）→ 记录 stash_ref | stash 失败→abort + escalate |
| 5. apply | 调用 `patch_applier.apply_file_changes(changes, workspace_root)` | apply 失败→`git stash apply <ref>` 回滚 + 计入 N |
| 6. 记录 session | `attempts[-1].patch_applied = {files, stash_ref, patch_hash}` | — |

内核改动风险标记：若 patch 含 `.c`/`Makefile`/`defconfig`/`init.rc`，标记 `attempts[-1].patch_applied.risk = "KERNEL"`，供 §7 阶段2增强检查使用。

#### `compile`

```
le control compile --session <sid>
```

职责：独立编译当前 workspace，不部署。

| 步骤 | 动作 | 失败处理 |
|---|---|---|
| 1. 读取 session.target | 确定编译目标 | — |
| 2. 调用 `deploy.compiler.compile_plan()` | 产出镜像 / 二进制到 `engineering/output/artifacts/<session>/` | 编译失败→记录 `compile_result=FAILED` + `failure_code=COMPILE_FAILED` + 错误日志路径 |
| 3. 记录 session | `attempts[-1].compile_result` + 产物路径 | — |

失败时不自动 revert（单一职责）——由主会话 AI 按 SOP 调 `le control revert`。

#### `revert`

```
le control revert --session <sid>
```

职责：回滚最近一次 apply-patch。

| 步骤 | 动作 |
|---|---|
| 1. 读 session | 取 `attempts[-1].patch_applied.stash_ref` |
| 2. git stash apply | `git stash apply <ref>` 恢复改动前状态 |
| 3. 记录 session | `attempts[-1].reverted = true` |

edge case：若 stash_ref 不存在或 apply 冲突，记录错误 + escalate。

### 5.3 session 结构增强

```json
{
  "session_id": "lciod-20260624120000",
  "workflow_id": "lciod-verify",
  "target": "lciod",
  "max_attempts": 5,
  "current_attempt": 2,
  "status": "RETRY",
  "attempts": [
    {
      "attempt_index": 1,
      "verify_result": "FAIL",
      "evidence_path": "<artifacts_dir>/evidence_bundle.json",
      "failed_cases": [
        {"id": "lciod.hal.init", "status": "fail", "failure_reason": "...", "command": "..."}
      ],
      "failure_code": "RUN_FAILED",
      "patch_applied": {
        "files": ["vendor/lciod/service.cpp"],
        "stash_ref": "stash@{0}",
        "patch_hash": "sha256:abc123...",
        "risk": "NORMAL"
      },
      "compile_result": "SUCCESS",
      "deploy_mode": "PUSH_SINGLE",
      "deploy_result": "SUCCESS",
      "reverted": false
    }
  ]
}
```

新增字段：`failed_cases`（G2）、`failure_code`（G5/policy）、`patch_applied.stash_ref`/`patch_hash`/`risk`（apply-patch）、`compile_result`（compile）、`deploy_mode`/`deploy_result`（deploy）、`reverted`（revert）。

---

## 6. A1 gen-cases 校验器设计

### 6.1 职责边界

`le gen-cases` **不调 LLM**。主会话 AI 生成 YAML 后调此命令校验合规性。

### 6.2 校验项

| 类别 | 校验内容 | 失败处理 |
|---|---|---|
| Schema | YAML 语法合法；顶层结构（suite/cases）正确；必填字段（id/run_on/assert）存在 | 报错 + 行号 |
| 断言类型 | assert.type ∈ 9 种合法类型 | 报错 + 合法类型列表 |
| 命名规范 | case id 格式 `<suite>.<capability>[.<sub>]`；无重复 id | 报错 + 冲突项 |
| 依赖 | requires 引用的 case id 存在；无环 | 报错 + 依赖链 |
| foreach | `${item}` 占位符与 parameters 列表项数量匹配 | 报错 |
| action | action:reboot 的 case 必须有 run_on:device 且断言含 boot marker 检测 | 警告（非致命，`--strict` 升级为错误） |

### 6.3 复用现有代码

校验逻辑大量复用 `case_loader.py`（load_suite 已做 include/requires/foreach 展开/FQN 解析/拓扑排序/环检测）。`gen-cases --validate` 实质是 case_loader 的薄封装 + 断言类型白名单校验。

### 6.4 CLI 接口

```
le gen-cases --validate <file|dir> [--strict]
  # --strict: 警告也作失败
  # 退出码: 0=合规, 1=有不合规项
```

---

## 7. 全自动 dd 四阶段前置防护网

### 7.1 设计原则

全自动 dd 的安全依赖**多层前置过滤**，越早拦截风险越低。在"补丁→编译产物→刷写前→刷写后"四阶段逐层验证，任一阶段失败即中止/回退，不盲目 dd。

### 7.2 阶段1：补丁静态验证（apply-patch 前已有，dd 场景增强）

| 检查项 | 拦截什么 | 实现 |
|---|---|---|
| 白名单 | 改无关文件 | patch_guard.py（§5.2） |
| 内核改动风险标记 | boot.img/kernel/ramdisk 改动触发更严格后置检查 | patch_guard 识别 `.c`/`Makefile`/`defconfig`/`init.rc` → 标记 `RISK=KERNEL` |

### 7.3 阶段2：编译产物验证（编译成功后、dd 前）

归属：`deploy/image_verify.py`（新增）。

| 检查项 | 拦截什么 | 实现 | 失败处理 |
|---|---|---|---|
| 镜像完整性 | boot.img/vendor.img 损坏/截断 | `file` 类型识别 + `sgdisk --verify`（GPT 完整性）+ sha256 自校验 | abort + revert + 计入 N |
| 镜像大小合理性 | 镜像异常膨胀/缩水（分区越界） | 与上次成功镜像对比，体积变化超 ±30% → 可疑 | abort + escalate |
| 内核符号表 | 内核编译缺少 exports（驱动 insmod 失败） | `nm`/`cat Module.symvers` 检查关键符号存在 | abort + revert + 计入 N |
| selinux 策略 | .te 编译的 sepolicy 中间产物语法错误 | 检查 `.pp` 文件生成 + `sepol_check` 退出码 | abort + revert + 计入 N |
| 内核改动增强（RISK=KERNEL 时） | 内核改动风险更高，额外检查 defconfig 一致性、禁用关键 driver 等 | 对比 defconfig diff，关键项变更 → escalate | escalate |

### 7.4 阶段3：刷写前设备状态验证（dd 前）

归属：`deploy/deployer.py` dd 前置检查。

| 检查项 | 拦截什么 | 实现 | 失败处理 |
|---|---|---|---|
| 设备健康基线 | 设备已半砖再 dd 会彻底死 | dd 前跑 smoke：`getprop sys.boot_completed=1` + 关键 service alive | 非健康 → abort + escalate |
| 备份完整性 | 备份镜像损坏，事后无法回退 | 备份后 sha256 校验备份镜像 | 损坏 → abort + escalate（宁不 dd 也不丢救命备份） |
| 磁盘空间 | /data/local/tmp 空间不足 | push 前 `df` 检查 | 不足 → abort + escalate |

### 7.5 阶段4：刷写后验证（dd 后 reboot）

归属：`deploy/deployer.py` + `deploy/rollback.py`（新增）+ serial 通道。

| 检查项 | 拦截什么 | 实现 | 失败处理 |
|---|---|---|---|
| boot_completed 超时 | kernel/init 起不来 | 120s 超时（adb_ops.wait_boot_completed） | 触发 serial 回退 |
| panic marker 检测 | kernel panic 但 boot_completed 假成功 | serial transcript 扫 panic_markers | 检测到 → 触发回退 |
| 关键 service 存活 | boot 完成但目标 service crash（半砖） | boot_completed 后跑 smoke case（如 lciod common） | 失败 → 触发回退 |
| serial shell 可达 | 事后回退的前提 | 回退前验证 serial 有 shell | 无 shell → escalate 人工 |

### 7.6 回退机制（rollback.py）

```
boot_completed 超时 / panic / 半砖
  → 切 serial 通道
  → 验证 serial 有 shell
     ├─ 有 shell → serial 执行：
     │    dd if=/data/local/tmp/backup_<sid>_<attempt>/boot.img of=/dev/block/...
     │    dd if=/data/local/tmp/backup_<sid>_<attempt>/vendor.img of=/dev/block/...
     │    reboot
     │    → 计入 N → goto Step 3
     └─ 无 shell（kernel 死）→ escalate 人工（物理重刷 SD）
```

### 7.7 防护链执行顺序（汇总）

```
[阶段1] 补丁白名单 + 风险标记
   ↓ (apply-patch)
[compile]
[阶段2] 镜像完整性 / 大小 / 符号表 / sepolicy / (RISK=KERNEL 增强)
   ├─ 任一失败 → revert + 计入 N → Step 3
   └─ 全通过 → 继续
[阶段3] 设备健康基线 / 备份完整性 / 磁盘空间
   ├─ 任一失败 → abort + escalate（不 dd）
   └─ 全通过 → dd
[阶段4] boot_completed / panic marker / 关键 service / serial shell
   ├─ panic / 半砖 / 超时 → serial dd 回退 + 计入 N → Step 3
   └─ kernel 死 → escalate 人工
```

---

## 8. 闭环 SOP（主会话 AI 操作手册）

> 本节是 C1 的核心交付物。完整的独立 SOP 文档（`docs/specs/2026-06-24-loop-auto-loop-sop.md`）将在实施阶段基于本节展开。此处给出 SOP 骨架。

### 8.1 前置条件

- 设备已 adb 可达（或 serial→adb 已 bootstrap，参见 `lcview-adb-run` workflow）
- workspace 有目标模块源码
- `cases/` 已有用例（或先执行 Step 0 生成）

### 8.2 主会话 AI 执行的 8 步

见 §4.1。AI 介入点：

| 步骤 | AI 职责 |
|---|---|
| Step 0 | 读 case-template.md + 目标模块源码 → 生成 YAML → 调 `le gen-cases --validate` 校验 |
| Step 4 | 读 analysis_request.json + evidence_bundle.json → 生成诊断 + patch.json（FileChange[]） |
| 其余步骤 | 调 control 子命令 + 读输出 + 按 SOP 决策下一步（含 escalate 判定） |

### 8.3 patch.json 格式

与 `analyzer_protocol.FileChange` 一致：

```json
[
  {
    "workspace_path": "vendor/lciod/service.cpp",
    "change_type": "edit",
    "old_marker": "原有代码片段",
    "new_content": "修复后代码片段"
  }
]
```

### 8.4 与 `/le` 命令集成

- `.opencode/commands/le.md` 当前注入 `@engineering/loop/WORKFLOW.md`
- 方案：WORKFLOW.md 保留为"架构/数据流/组件索引"SSOT；新增独立 SOP 文档聚焦"主会话 AI 操作手册"
- `/le` 命令同时注入两者，或 WORKFLOW.md 顶部链接到 SOP

### 8.5 WORKFLOW.md 更新点

| 章节 | 改动 |
|---|---|
| 核心流程（L10-21） | 替换为 §4.1 的 8 步全自动流程 |
| 遗留点（L254-261） | gen-cases 标记为"已实现（校验器）"；删除 deploy/loop_ctrl 遗留标记 |
| 新增章节 | "部署约束：能 PUSH_SINGLE 不 dd boot.img"（§4.2）+ "全自动 dd 四阶段防护网"（§7） |

---

## 9. 文件变更总清单

### 9.1 新增文件（6 个）

| 文件 | 用途 |
|---|---|
| `docs/specs/2026-06-24-loop-auto-loop-sop.md` | C1 完整闭环 SOP 文档（主会话 AI 操作手册，基于 §8 展开） |
| `engineering/loop/config/target-paths.yaml` | 补丁白名单 target → allowed_prefixes 映射 |
| `engineering/loop/controller/python/loop_controller/patch_guard.py` | 白名单校验 + 语法检查 dispatch + 风险标记 |
| `engineering/loop/controller/python/loop_controller/compile_cmd.py` | compile / revert 子命令实现（或合入 control_cli） |
| `engineering/loop/deploy/python/loop_deploy/image_verify.py` | 阶段2 编译产物验证 |
| `engineering/loop/deploy/python/loop_deploy/rollback.py` | dd 备份 + serial 回退逻辑 |

### 9.2 修改文件（9 个）

| 文件 | 改动摘要 |
|---|---|
| `engineering/loop/WORKFLOW.md` | 核心流程→8 步全自动；遗留点更新；新增部署约束 + dd 防护网章节 |
| `engineering/loop/controller/python/loop_controller/control_cli.py` | 修 G1/G2/G5；新增 apply-patch/compile/revert 子命令；session 结构增强 |
| `engineering/loop/controller/python/loop_controller/cycle_orchestrator.py` | 补 failed_cases 提取逻辑；补 patch_hash 去重 |
| `engineering/loop/contracts/python/loop_contracts/failure_codes.py` | 新增 COMPILE_FAILED / PATCH_REJECTED / BOOT_TIMEOUT_ROLLBACK |
| `engineering/loop/core/python/loop_core/cli.py` | gen-cases 从占位 → `--validate` 校验器实现 |
| `engineering/loop/deploy/python/loop_deploy/deployer.py` | dd 前置检查（阶段3）+ 刷写后验证（阶段4）+ 嵌入 rollback 调用 |
| `engineering/loop/deploy/python/loop_deploy/adb_ops.py` | wait_boot_completed 超时返回状态而非直接报错 |
| `engineering/loop/deploy/python/loop_deploy/models.py` | 新增 BOOT_TIMEOUT_ROLLBACK 状态 |
| `engineering/loop/README.md` | 更新遗留点状态 |

### 9.3 不做（YAGNI）

| 项 | 理由 |
|---|---|
| `loop_workflows/python` 空骨架填充 | 当前编排由主会话 AI 按 SOP 承担；骨架为未来独立 driver 预留，YAGNI |
| `LlmAnalyzer` 具体子类实现 | 主会话 AI 即 Analyzer；ABC 仅预留 |
| `FLASH_FULL` 物理刷机自动化 | 受物理边界限制，kernel 死时软件无法自救 |

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AI 生成错误补丁导致编译失败 | 中 | 低（自动 revert） | 编译失败自动回滚 + 计入 N |
| AI 生成错误补丁通过编译但运行时刷砖 | 低 | 高（设备不可用） | 四阶段防护网（§7）+ serial 回退 + kernel 死 escalate |
| AI 反复生成相似补丁死循环 | 中 | 中（浪费时间） | patch_hash 去重 + N=5 + REPEATED_FAILURE |
| serial 回退本身失败（serial 通道异常） | 低 | 高（无法自救） | 回退前验证 serial shell 可达；失败 escalate |
| stash apply 冲突 | 低 | 中 | 理论上连续改动不会冲突；冲突 escalate |
| 备份镜像损坏 | 低 | 高（无法回退） | 阶段3 备份完整性校验，损坏 abort |

---

## 11. 验收标准

1. `le gen-cases --validate <file>` 能正确校验现有 lciod/lcview 用例，退出码符合预期。
2. `le control init → run-verify → decide → analyze-request → apply-patch → compile → deploy` 全链路可串联执行（可先用 FixtureTransport 离线验证）。
3. apply-patch 白名单拒绝越界补丁（PATCH_REJECTED）。
4. compile 失败 → revert 成功 → workspace 恢复。
5. deploy PUSH_SINGLE 正常（能推送不 dd）。
6. deploy DD_BOOT_REBOOT：备份成功 + 镜像验证通过 + dd + boot_completed 正常。
7. deploy DD_BOOT_REBOOT 模拟超时 → serial 回退成功（需 serial 通道可用）。
8. N=5 / 同 failure_code 重复 / patch_hash 重复 → escalate。
9. WORKFLOW.md 核心流程章节更新完成，遗留点章节更新完成。

---

## 12. 实施顺序建议

| 阶段 | 内容 | 依赖 |
|---|---|---|
| P1 | A1 gen-cases --validate 校验器 | 无 |
| P2 | control_cli G1/G2/G5 修复 + session 结构增强 | 无 |
| P3 | apply-patch / compile / revert 子命令 + patch_guard + target-paths.yaml | P2 |
| P4 | deploy 四阶段防护网 + image_verify + rollback | P3 |
| P5 | cycle_orchestrator patch_hash 去重 + policy 接入完成 | P2 |
| P6 | WORKFLOW.md 更新 + SOP 文档 | P1-P5 |

---

## 附录 A：与历史设计文档的关系

| 历史文档 | 关系 |
|---|---|
| `2026-06-19-loop-engineering-v2-design.md` | 本设计继承其"用例驱动 + EvidenceBundle"架构，升级其"半闭环"为全自动 |
| `2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md` | 本设计**取消**其 §1"人工确认补丁"约束，改为全自动 apply + 护栏 |
| `2026-06-22-lciod-loop-verification-design.md` | 本设计**实现**其 §4 P2（deploy）+ §5 P3（controller），并升级 FLASH_FULL 处理 |
| `engineering/loop/WORKFLOW.md` | 本设计**重写**其核心流程章节，更新遗留点 |
