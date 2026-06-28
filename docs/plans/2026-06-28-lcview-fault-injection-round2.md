# LcView 故障注入 × LE v2 全能力验证（第 2 轮）实施计划

> **For agentic workers:** 本计划按**风险递增**顺序执行（用户指令）。纯软件 Task 走 TDD；真机闭环 Task 走「注入→`le runtime run`→校验终态→回滚」。流程中所有问题自行修复，仅高风险不可逆物理操作 / 无权限 / 独占资源决策时请用户介入。

**Goal:** 通过 6 类全新故障端到端验证 LE v2 全能力，并修复探索中发现的框架缺陷。

**Architecture:** 干净 git 基线注入故障 → runtime 状态机自动闭环 → 校验 terminal_state/attempt/checkpoint → git 回滚 → 下一故障。真机 live 模式，IP 由串口动态发现。

**Tech Stack:** Python(pytest) + C++(AOSP mmm) + bash(le.sh) + RPi5 串口/adb。

**统一环境变量：**
```
PYTHONPATH=engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python
```
全量回归命令：`python3 -m pytest engineering/loop -q --import-mode=importlib`

---

### Task 0: 框架缺陷#1 测试污染（最低风险）✅ 已完成

**Files:** `engineering/loop/controller/python/tests/test_runtime_engine.py:676-707`
- [x] 根因：`test_rollback_deploy_uses_adb_endpoint` 裸 `sys.modules[...]=` 注入假 loop_adb 未恢复，污染 deploy 测试
- [x] 修复：改 `monkeypatch.setitem/setattr` 自动恢复
- [x] 验证：全量 `pytest engineering/loop` = **515 passed, 0 failed**（原 508+7failed）

---

### Task 1: §5.2 dmesg 基线退化修复

**Files:** `engineering/loop/cases/features/lcview/common.yaml`（`lcview_kernel_module_loaded`）+ `kernel_driver.yaml`（`ke02_module_init_log` / `ke03` / `ke15`）

- [ ] **Step 1**：读 common.yaml 的 `lcview_kernel_module_loaded` 当前 command（基于 `dmesg | grep`）与 assert
- [ ] **Step 2**：改为不依赖 dmesg 环形缓冲的检查——优先 `cat /proc/modules | grep <module>` 或 `lsmod | grep`，或 `ls /sys/module/<module>` / `ls /sys/class/lcview`。保留语义（模块已加载）
- [ ] **Step 3**：gen-cases 校验：`bash engineering/loop/scripts/le.sh gen-cases --validate engineering/loop/cases/features/lcview/common.yaml`
- [ ] **Step 4**：真机 fixture/live 验证该 case 在「设备长运行（dmesg 已滚出）」场景仍 PASS
- [ ] **Step 5**：README 同步检查（cases 语义变更）

---

### Task 2: N1 — KB-miss → 新增 ScriptedAnalyzer 规则（PUSH_SINGLE，低风险）

**Files:**
- 注入：`~/workspace/.../daemon/lechao_lcview.cpp:162`（解析循环入口插 `break; //FAULT-INJECTED-N1`）
- 规则：`engineering/loop/controller/python/loop_controller/.../analyzer_protocol.py`（新增 `_rule_lcview_parse_loop_break`，追加到 `_RULES`）
- 测试：`engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py`

- [ ] **Step 1（TDD）**：在 test_lcview_analyzer_rules.py 写失败测试——构造 failed case（case_id=`lcview_daemon_read_loop_active` 含 `NO_LOOP`，或 reason 含 `FAULT-INJECTED-N1`），断言新规则返回 target_files 含 lechao_lcview.cpp、confidence=0.95、动作=删除注入的 break 行
- [ ] **Step 2**：跑测试确认 FAIL（规则未定义）
- [ ] **Step 3**：读 analyzer_protocol.py:154-191 现有 `_rule_lcview_hal_connect_fault` 作模板，实现 `_rule_lcview_parse_loop_break`（匹配条件如上，修复=删除含 `FAULT-INJECTED-N1` 的行），追加到 `_RULES`
- [ ] **Step 4**：跑测试确认 PASS；跑全量 pytest 无回归
- [ ] **Step 5（端到端）**：git 干净基线 → 注入 break 故障 → `le.sh runtime init --target lcview --suite end_to_end.yaml --max-attempts 5 --artifacts-dir engineering/output/runs/round2-N1-<ts>` → `runtime run --session <s> --adb-endpoint <ip:5555>`
- [ ] **Step 6**：校验 terminal_state=DONE_SUCCESS；KB 命中=miss、Scripted 命中；attempt 历史含 APPLY→COMPILE→DEPLOY(push_single)
- [ ] **Step 7**：确认成功补丁归档 KB（新 fingerprint）；git checkout 回滚故障

---

### Task 3: N2 — KB/Scripted-miss → Opencode LLM（PUSH_SINGLE，中风险）

**Files:** 注入 `~/workspace/.../config/lcview_events.json`（某字段 `"type":"int64"`→`"int32"`）

- [ ] **Step 1**：选定一个内核会写 8 字节的字段（如 id=4 的 device_index），改 type→int32
- [ ] **Step 2**：git 基线 → 注入 → `runtime init/run`（suite=end_to_end，捕获 E11 invalid 暴增）
- [ ] **Step 3**：校验三层降级：KB miss → Scripted miss → OpencodeAnalyzer(0.8) 产出 JSON 配置补丁（type 改回 int64），rationale 标 `[OpencodeAnalyzer]`
- [ ] **Step 4**：校验 patch APPLIED→COMPILE→DEPLOY→RUN_VERIFY；terminal_state=DONE_SUCCESS
- [ ] **Step 5**：git checkout 回滚；归档证据

---

### Task 4: N3 — duplicate_patch_hash guard（PUSH_SINGLE，中风险）

**Files:** 双注入：`lechao_lcview.cpp`（N1 同款日志故障，被 Task2 新规则匹配产固定补丁）+ `FileWriter.cpp:109`（文件名故障，使 verify 恒 FAIL）

- [ ] **Step 1**：注入两处故障 → `runtime init/run`
- [ ] **Step 2**：校验 attempt1 产补丁 hash=H → verify 仍 FAIL（filename 未修）→ RETRY；attempt2 产同补丁 hash=H → DECIDE_NEXT 命中 `duplicate_patch_hash`（优先级高于 progress_converging）→ ESCALATE_HUMAN
- [ ] **Step 3**：校验 checkpoint 记录 matched_guards 含 duplicate_patch_hash；failure_code=DUPLICATE_PATCH
- [ ] **Step 4**：git checkout 回滚双故障；归档证据

---

### Task 5: N4 — progress_converging 收敛态（PUSH_SINGLE，中风险）

**Files:** 注入 2~3 个互相独立、可被不同 analyzer 层逐个修复的 verify 故障

- [ ] **Step 1**：选 2 个独立故障（如 N1 日志故障 + N2 schema 故障），使每轮只收敛 1 个
- [ ] **Step 2**：`runtime init/run` → 校验 failed 数逐轮递减（如 2→1→0），progress_converging 在 latest<previous>0 时判 RETRY
- [ ] **Step 3**：校验最终 terminal_state=DONE_SUCCESS；attempt 历史体现收敛轨迹
- [ ] **Step 4**：（回归态/卡住态）确认已有单测 `test_progress_converging_escalates_when_failures_*` 覆盖；如需补充则补单测
- [ ] **Step 5**：git checkout 回滚；归档证据

---

### Task 6: N5 — human gate 端到端（PUSH_SINGLE，中风险）

**Files:** 临时改 `engineering/loop/.../analyzer.yaml`（threshold 0.7→0.9）+ 复用 N2 类 Opencode 故障

- [ ] **Step 1**：临时调 analyzer.yaml confidence threshold→0.9（git 可还原）
- [ ] **Step 2**：注入 N2 类故障（Opencode 0.8 < 0.9）→ `runtime init/run`
- [ ] **Step 3**：校验 run 在 APPLY_PATCH 前暂停，session.pending_human_gate=True
- [ ] **Step 4**：`le.sh runtime pending --session <s>` 显示待确认补丁；`runtime approve --session <s>` 续跑
- [ ] **Step 5**：校验 approve 后走完 APPLY→COMPILE→DEPLOY→DONE_SUCCESS；另跑一次 `reject` 验证 ESCALATE 路径
- [ ] **Step 6**：`git checkout analyzer.yaml` 还原 threshold；git checkout 回滚故障；归档证据

---

### Task 7: N6 — DD_BOOT_REBOOT 真机（高风险，最后做）⚠️ dd 前必须用户确认

**Files:** 注入 `~/workspace/.../daemon/lechao_lcview.rc`（追加 `service lechao_fault_test /system/bin/nonexistent_fault_binary` oneshot）+ 新增 verify case `lcview_no_fault_service`（common.yaml）

- [ ] **Step 1（TDD/case）**：common.yaml 新增 `lcview_no_fault_service`（`getprop init.svc.lechao_fault_test` 应为空/未定义），gen-cases 校验
- [ ] **Step 2**：注入 .rc 故障 → `le.sh deploy --decide --diff-rev HEAD` dry-run 确认 decider 判定 = DD_BOOT_REBOOT
- [ ] **Step 3**：`runtime init/run` → COMPILE 走 `mk_rpi5_full_image.sh -mode 2` 产 boot.img
- [ ] **Step 4 ⚠️ 介入点**：DEPLOY 进入 dd 前，**暂停并向用户确认**（不可逆物理写入 boot 分区，变砖风险）。确认后继续
- [ ] **Step 5**：校验四阶段防护网逐项：push→sha256→image_verify(+备份)→device_health；host+device 双备份生成
- [ ] **Step 6**：dd 写入 → reboot → 校验 boot_completed(120s)；串口可达
- [ ] **Step 7**：校验 verify 捕获 `lcview_no_fault_service` FAIL（或部署链路+防护网走通即可，按 spec §3.1 默认不强制修复闭环）
- [ ] **Step 8**：验证 serial 回滚链路（serial_rollback_dd 或 dd 回原 boot 备份）；git checkout 回滚 .rc 故障
- [ ] **Step 9**：归档证据；若 boot 异常 → 用 SD 物理重刷兜底

---

### Task 8: 收尾 — 报告 + 归档 + 全量回归

**Files:** `docs/specs/2026-06-28-lcview-fault-injection-round2-report.md`

- [ ] **Step 1**：写 round2 验证报告（N1-N6 证据矩阵 + 过程修复的框架缺陷 + 未验证项）
- [ ] **Step 2**：确认 KB 增长（N1 新 fingerprint）、新 Scripted 规则 + TDD 通过
- [ ] **Step 3**：全量回归 `pytest engineering/loop` 绿 + `make lechao_lcview_unit_test lechao_lcview_hal_test` 编译通过
- [ ] **Step 4**：确认 workspace `vendor/lechao` 回到干净基线（git status 空）；analyzer.yaml 已还原
- [ ] **Step 5**：README 同步检查（engineering/harness/ 或 cases 变更）
