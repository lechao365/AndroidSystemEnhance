# LcView 故障注入 × Loop Engineering 全能力验证设计规格

**日期：** 2026-06-28
**状态：** approved
**作者：** opencode（brainstorming 产出）

---

## 1. 背景与动机

### 1.1 Loop Engineering v2 框架现状

本项目的 loop engineering v2 框架已实现完整的自动修复闭环：

```
runtime init → RUN_VERIFY → DECIDE_NEXT
  → PASS → DONE_SUCCESS（补丁归档到 KB）
  → RETRY → ChainedAnalyzer（KB 0.98 → Scripted 0.95 → Opencode 0.8）
            → APPLY_PATCH（worktree 隔离）→ COMPILE → DEPLOY → 回到 VERIFY
  → 终态 → ESCALATE_HUMAN
```

框架具备的能力清单：
- 三层降级 analyzer（fingerprint 匹配 / 确定性规则 / LLM subprocess）
- 补丁隔离（git worktree 优先，git stash 降级）
- 白名单 + 语法预检 + 风险评估
- 两种部署模式（PUSH_SINGLE 秒级 push binary / DD_BOOT_REBOOT 内核改动 dd+reboot）
- 四阶段防护网（白名单 → 镜像校验 → 设备健康基线 → boot_completed+panic 检测）
- 结构化失败码 + 收敛 guard（progress_converging / repeated_failure / duplicate_patch）
- 串口传输（rp5-serial daemon）+ 设备 IP 动态发现
- human-in-loop gate（低置信度补丁需人工 approve）

### 1.2 已发现的 3 个 gap

通过代码审查发现框架在 lcview 模块场景下存在 3 个 gap：

| Gap | 根因 | 影响 |
|-----|------|------|
| **G1** | `~/workspace/aosp/vendor/lechao` 不是 git 仓库（不在 repo manifest 管理） | loop runtime 的 worktree 隔离、git stash 回滚全部失效；patch 落盘后无回滚能力 |
| **G2** | `harness_observability.sh` 的 `HARNESS_EXCLUDE_*` 常量未排除 `.git` | G1 修复后，`lc-sync-code-to-patchs` 会把 `vendor/lechao/.git/` 全量复制到 `patchs/`，污染归档 |
| **G3** | `engine.py:183` 和 `nodes.py:23-27` 硬编码 `~/workspace/aosp` 作为 ws_root | `~/workspace/aosp` 是 repo 聚合树（32G，非 git），worktree 创建必然失败；即使 G1 修复后 vendor/lechao 是 git 仓库，runtime 仍定位到错误的根 |

### 1.3 验证需求

当前知识库（`patch_knowledge_base.json`）仅有 1 条 lcview 记录，未系统性验证框架的全部能力。需要通过可控的故障注入，覆盖：

- 三层 analyzer 的每一层（KB / Scripted / Opencode）
- 两种部署模式（PUSH_SINGLE / DD_BOOT_REBOOT）
- 回滚链路（worktree 回滚 / 编译失败回滚）
- 防护网（四阶段安全检查 / 收敛 guard）
- 串口诊断（设备不可达时的 serial_shell 回滚）

---

## 2. 目标

### 2.1 核心目标

1. **修复 3 个 gap**，使 loop runtime 在 lcview 模块场景下完全可用
2. **注入 6 类递进故障**，覆盖 loop engineering v2 的全部能力
3. **过程中发现的问题即时修复**，与业界 loop engineering 框架对齐

### 2.2 非目标（Out of Scope）

- lcview 模块自身的功能增强（仅注入故障，不改进 lcview 代码质量）
- loop 框架的架构重构（仅修复 gap，不改架构）
- 其他模块（lciod / system.boot）的故障注入（仅 lcview + 必要的 init.rc 改动）

---

## 3. 方案设计

### 3.1 Gap 修复方案

#### G1 修复：vendor/lechao 本地 git 仓库

**决策：** 在 `~/workspace/aosp/vendor/lechao/` 创建本地 git 仓库（不 track 远端）。

**理由：**
- `vendor/lechao` 仅 500K / 57 文件（全部是源码，无编译产物），`git init + add -A + commit` 秒级完成
- 给 loop runtime 的 worktree 隔离提供 git 基础
- 不影响 repo 工具的管理（vendor/lechao 不在 manifest 中，repo 不会触碰它）
- 编译不受影响（AOSP 的 mmm/make 不依赖 vendor/lechao 的 git 状态）

**仓库层级选择：** `vendor/lechao`（含 services/ + Android.bp），而非更细粒度的 services/lechao_lcview，因为：
- worktree 创建时得到完整的 vendor/lechao 快照（含所有 lechao 自研代码）
- 未来 lciod 等模块也能复用同一个 git 仓库
- 单一 git 仓库维护成本低

#### G2 修复：排除 .git 的同步污染

**决策：** 修改 `harness_observability.sh` 的两个常量。

**当前值：**
```bash
HARNESS_EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'
HARNESS_EXCLUDE_DIR_RE='^(out|prebuilts)$'
```

**修改后：**
```bash
HARNESS_EXCLUDE_RE='...|^\.git/'
HARNESS_EXCLUDE_DIR_RE='^(out|prebuilts)$|^\.git$'
```

**为什么改常量而非改脚本逻辑：**
- `HARNESS_EXCLUDE_*` 是 sync + revert 两个脚本的**共享单一事实源**（已 grep 确认 19 处引用）
- 改一处常量，两个脚本的 `find -type f | grep -vE` 和 `_discover_non_repo` 全部生效
- 符合 DRY 原则，不引入脚本级分支逻辑

#### G3 修复：worktree ws_root 下沉到 vendor/lechao

**决策：** 新增 `ENV_LE_PATCH_GIT_ROOT` 环境变量，让 patch 相关的 git 操作定位到 vendor/lechao。

**架构：**
```
le.sh 启动时 export LE_PATCH_GIT_ROOT=$HOME/workspace/aosp/vendor/lechao
  ↓
engine.py APPLY_PATCH 节点：
  ws_root = os.environ.get("LE_PATCH_GIT_ROOT") or AOSP_ROOT fallback
  create_patch_worktree(ws_root, ...)  ← 在 vendor/lechao 上创建 worktree
  ↓
nodes.py _workspace_root()：
  同样优先读 LE_PATCH_GIT_ROOT
  ↓
patch_applier.apply_file_changes(changes, apply_root)：
  apply_root = worktree_path（vendor/lechao 的 worktree 快照）
  fc.workspace_path = "vendor/lechao/services/lechao_lcview/..."（相对路径）
  实际写入 worktree_path/vendor/lechao/services/... ← 正确
```

**关键设计决策：workspace_path 的路径处理**

FileChange.workspace_path 是相对于 AOSP 根的路径（如 `vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp`）。worktree 创建在 vendor/lechao 级别时，worktree_path 就是 vendor/lechao 的快照。因此 apply 时需要：

```python
# apply_root = worktree_path（vendor/lechao 快照根）
# fc.workspace_path = "vendor/lechao/services/.../file.cpp"
# 需要截取 vendor/lechao 之后的相对路径
rel_path = fc.workspace_path[len("vendor/lechao/"):]  # "services/.../file.cpp"
fp = Path(apply_root) / rel_path
```

这需要 `patch_applier.apply_file_changes` 或 `nodes.node_apply_patch` 做 path prefix strip。**本设计采用在 node_apply_patch 层面做 strip**（而非改 patch_applier），因为：
- patch_applier 是通用工具，不应耦合 vendor/lechao 前缀知识
- node_apply_patch 已知道 worktree_handle 和 ws_root，有足够上下文做 strip

### 3.2 故障注入设计

#### 设计原则

1. **递进风险**：低风险（日志污染）→ 中风险（数据链路）→ 中高风险（编译失败）→ 高风险（boot 失败）
2. **可控性**：每个故障可秒级注入、秒级回滚（git checkout）
3. **真实性**：故障模式模拟真实开发中的常见 bug（魔法数错误、event_id 偏移、命名规则破坏等）
4. **覆盖性**：每个故障针对一个 loop 能力维度，不重复

#### 6 类故障

| # | 故障 | 注入位置 | 触发的 loop 能力 | analyzer 层 | deploy 模式 | 风险 |
|---|------|---------|-----------------|------------|-------------|------|
| F1 | daemon validate failed 日志 | lechao_lcview.cpp main loop | KB fingerprint 匹配 + DONE_SUCCESS 归档 | KB 0.98 | PUSH_SINGLE | 低 |
| F2 | HAL connect 失败日志 | LcView.cpp bind_hal | ScriptedAnalyzer 规则（新增） | Scripted 0.95 | PUSH_SINGLE | 低 |
| F3 | schema event_id 偏移 | lcview_events.json | OpencodeAnalyzer LLM subprocess | Opencode 0.8 | PUSH_SINGLE | 中 |
| F4 | FileWriter 命名破坏 | FileWriter.cpp makeFilename | progress_converging guard + LLM | Opencode 0.8 | PUSH_SINGLE | 中 |
| F5 | C++ 编译错误 | lechao_lcview.cpp | COMPILE_FAILED → REVERT_PATCH → ESCALATE | 无 | 无（编译失败） | 中高 |
| F6 | init.rc 无效 service | init.rpi5.rc | DD_BOOT_REBOOT + 四阶段防护网 + 串口 | 无 | DD_BOOT_REBOOT | 高 |

#### F1: daemon validate failed 日志

**注入：** 在 `lechao_lcview.cpp` 的 main loop 入口前插入 `ALOGE("parse: validate failed: bad magic")`。

**验证：** KB 已有此 fingerprint（`validate failed: bad magic`），confidence=0.98。runtime 应：
1. RUN_VERIFY 发现 daemon 日志含 bad magic → case FAIL
2. ChainedAnalyzer 第一层 KB 命中 → 产出删除该行的补丁
3. APPLY_PATCH + COMPILE + DEPLOY（PUSH_SINGLE）
4. 第二次 RUN_VERIFY PASS → DONE_SUCCESS
5. 补丁 hit_count +1（若 fingerprint 已存在则累加）

#### F2: HAL connect 失败日志

**注入：** 在 `LcView.cpp` 的 bind_hal lambda 后插入 `ALOGE("connect failed: cannot cast to ILcView")`。

**验证：** 需新增 ScriptedAnalyzer 规则 `_rule_lcview_hal_connect_fault`，匹配条件：
- failure_reason 含 "connect failed" 或 "cannot cast to ILcView"
- command 涉及 lechao_lcview_hal

confidence=0.95，修复动作：删除注入的故障日志行。

#### F3: schema event_id 偏移

**注入：** 修改 `lcview_events.json` 第一个事件的 id 从 4 改为 14（内核仍写 4，schema 期望 14 → validate 失败 → jsonl 不生成）。

**验证：** KB 和规则均无此 fingerprint → OpencodeAnalyzer 被调用。LLM 需分析 evidence（jsonl 缺失 + schema JSON 内容）生成修复补丁（把 id 改回 4）。confidence=0.8，可能触发 human gate。

#### F4: FileWriter 命名规则破坏

**注入：** 修改 `FileWriter.cpp` 的 `makeFilename`，将 `_{schema.name}` 改为 `_unknown_fault`，导致 jsonl 文件名不符合 `{event_id}_{event_name}_{date}_p{seq}.jsonl` 规则。

**验证：** `lcview_jsonl_filename_rule` 用例失败。验证 progress_converging guard（若第一次 attempt 修复了部分问题，失败用例数下降，宽限 RETRY 而非立即 ESCALATE）。

#### F5: C++ 编译错误

**注入：** 在 `lechao_lcview.cpp` 插入缺少分号的语句（`int fault_missing_semicolon = 42`）。

**验证：** analyzer 产出补丁（可能错误），APPLY_PATCH 成功，COMPILE_FAILED（缺分号）。runtime 应：
1. 检测到 COMPILE_FAILED
2. 触发 REVERT_PATCH（worktree 回滚，源码恢复）
3. DECIDE_NEXT → 因 compile_failed_but_recoverable → RETRY 或 ESCALATE

**关键验证点：** worktree 回滚后源码必须干净（无残留补丁）。

#### F6: init.rc 无效 service

**注入：** 在 `init.rpi5.rc` 追加 `service lechao_fault_test /system/bin/nonexistent_fault_binary`。

**验证：**
1. decider 识别为 .rc 改动 → DD_BOOT_REBOOT 模式
2. COMPILE 走 `mk_rpi5_full_image.sh -mode 2`（重建 boot.img）
3. DEPLOY 走 dd + reboot 全链路
4. 四阶段防护网逐项检查：
   - 镜像完整性校验（size/sha）
   - 设备健康基线（boot_completed=1 before dd）
   - boot 备份（host + device 双备份）
   - boot_completed 超时检测（120s）
5. 若 boot 超时 → BOOT_TIMEOUT → ESCALATE_HUMAN
6. 串口诊断 + serial_rollback_dd 回滚

**风险控制：** 无效 service 是 oneshot，不会导致 kernel panic（init 会标记失败但继续 boot）。即使 boot 慢，120s 超时后串口仍可达。

---

## 4. 架构影响

### 4.1 新增组件

无新组件。所有修改都在现有文件内。

### 4.2 修改的组件

| 组件 | 修改类型 | 影响范围 |
|------|---------|---------|
| `~/workspace/aosp/vendor/lechao/.git` | 新增（git init） | 仅 workspace，不影响 repo |
| `harness_observability.sh` | 常量修改 | sync + revert 脚本（共享常量） |
| `harness-paths.conf` | 新增 KEY | le.sh 启动时读取 |
| `engine.py` | ws_root 定位 | APPLY_PATCH 节点 |
| `nodes.py` | ws_root 定位 | _workspace_root() |
| `node_apply_patch`（nodes.py） | workspace_path prefix strip | worktree 模式下路径截取 |
| `analyzer_protocol.py` | 新增规则 | ScriptedAnalyzer 规则列表 |
| `le.sh` | export LE_PATCH_GIT_ROOT | 启动时环境变量 |

### 4.3 向后兼容性

- `LE_PATCH_GIT_ROOT` 缺省时回退到 `AOSP_ROOT`，旧环境不受影响
- `HARNESS_EXCLUDE_*` 新增 `.git` 排除，不影响已有排除规则
- 新增 analyzer 规则追加到 `_RULES` 列表末尾，不影响已有规则优先级
- `vendor/lechao` git 仓库是本地的，repo sync 不会触碰它

---

## 5. 测试策略

### 5.1 单元测试

- `test_lcview_analyzer_rules.py`：新增的 ScriptedAnalyzer 规则
- 现有 `test_workspace_isolation.py`：worktree 创建/删除（确认 vendor/lechao 可识别为 git）
- 现有 `test_patch_applier.py`：路径 prefix strip 逻辑

### 5.2 集成测试（端到端）

每个故障注入就是一个端到端测试：
- 注入故障 → `le runtime init` → `le runtime run` → 验证终态 + attempt 历史
- EvidenceBundle JSON 作为测试产物归档

### 5.3 回归测试

- `pytest engineering/loop/`：全量 Python 单元测试
- `make lechao_lcview_unit_test`：C++ 单元测试编译验证
- `lc-sync-code-to-patchs --check-only`：确认 .git 不被同步

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| DD_BOOT 导致设备变砖 | 低 | 高（需物理重刷） | 无效 service 是 oneshot 不致命；host+device 双备份；串口始终可达；SD 卡物理重刷准备 |
| OpencodeAnalyzer LLM 生成错误补丁 | 中 | 低（worktree 隔离+白名单保护） | human gate 机制（confidence<0.7 需 approve）；语法预检 |
| vendor/lechao git 仓库冲突 repo 管理 | 极低 | 低 | vendor/lechao 不在 manifest；repo forall 不会触碰 |
| 设备 IP 变化（DHCP） | 高 | 低（串口可重新发现） | 每次 reboot 后 `rp5_serial_helper.py device-ip` 重新获取 |
| 编译环境未 source envsetup | 中 | 中 | compiler.py 已在 cmd 中 source envsetup + lunch |

---

## 7. 成功标准

1. **G1-G3 修复完成**：vendor/lechao git 仓库可用；sync 不污染 .git；worktree 秒级创建
2. **F1-F4 故障自动收敛**：runtime terminal_state=DONE_SUCCESS
3. **F5 编译失败正确回滚**：worktree 清理干净，源码无残留
4. **F6 DD_BOOT 防护网触发**：BOOT_TIMEOUT 或 KERNEL_DEAD 检测到，ESCALATE_HUMAN
5. **知识库增长**：成功的补丁自动归档到 patch_knowledge_base.json
6. **全量回归 PASS**：pytest + C++ 单元测试无回归
