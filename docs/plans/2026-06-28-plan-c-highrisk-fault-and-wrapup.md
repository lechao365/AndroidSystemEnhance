# Plan C: 高风险故障 + 收尾归档（Task 9-11）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 注入编译错误（验证 worktree 回滚）+ init.rc 改动（验证 DD_BOOT 四阶段防护网）+ 收尾归档。

**Spec:** `docs/specs/2026-06-28-lcview-fault-injection-loop-validation-design.md` §3.2 F5-F6

**前置：** Plan A（Task 1-4）+ Plan B（Task 5-8）全部完成。

> ⚠️ **Task 10 高风险警告：** 涉及 dd boot.img + reboot，有变砖风险。执行前确保 SD 卡可物理重刷、串口始终可用。

---

## Task 9: 故障 F5 — 编译错误注入（COMPILE_FAILED → REVERT_PATCH 闭环）

**目标：** 注入 C++ 编译错误，验证 worktree 隔离回滚 + compile_failed guard + 源码恢复正确性。

**验证能力清单：**
- [x] worktree 隔离（编译失败不影响主 workspace）
- [x] COMPILE_FAILED failure_code 传播
- [x] REVERT_PATCH 回滚（worktree remove）
- [x] compile_failed_but_recoverable guard

**Files:**
- Modify（临时）: `~/workspace/aosp/vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp`

- [ ] **Step 1: 注入编译错误（缺少分号）**

```bash
cd ~/workspace/aosp/vendor/lechao
python3 << 'EOF'
p = 'services/lechao_lcview/daemon/lechao_lcview.cpp'
c = open(p).read()
marker = 'ALOGI("lechao_lcview: starting");'
if marker not in c:
    raise SystemExit("marker not found")
if 'FAULT-INJECTED' in c:
    raise SystemExit("previous fault not cleaned")
# 注入：缺少分号的语句（导致编译错误）
fault = marker + '''
    // FAULT-INJECTED: 编译错误（缺少分号）
    int fault_missing_semicolon = 42'''
assert c.count(marker) == 1
open(p, 'w').write(c.replace(marker, fault, 1))
print('compile error F5 injected')
EOF
git diff --stat
```

- [ ] **Step 2: 确认编译会失败（可选预检）**

```bash
cd ~/workspace/aosp
source build/envsetup.sh 2>/dev/null && lunch aosp_rpi5-bp1a-userdebug 2>/dev/null
mmm vendor/lechao/services/lechao_lcview -j$(nproc) 2>&1 | tail -5
# 预期：编译错误（expected ';' after ...）
```

注意：此预检会污染 workspace 编译状态，可选跳过。若跳过，直接进入 Step 3 由 runtime 触发编译。

- [ ] **Step 3: runtime 自动闭环**

```bash
ARTIFACTS=engineering/output/runs/lcview-f5-compile-$(date +%Y%m%d%H%M%S)
mkdir -p $ARTIFACTS
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)

bash engineering/loop/scripts/le.sh runtime init \
  --target lcview \
  --suite engineering/loop/cases/features/lcview/common.yaml \
  --max-attempts 3 \
  --artifacts-dir $ARTIFACTS

export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
bash engineering/loop/scripts/le.sh runtime run \
  --session $ARTIFACTS/session.json \
  --adb-endpoint $DEV_IP:5555 2>&1 | tee $ARTIFACTS/runtime-run.log
```

- [ ] **Step 4: 验证 COMPILE_FAILED → REVERT_PATCH 链路**

```bash
python3 << EOF
import json
s = json.load(open('$ARTIFACTS/session.json'))
print(f'terminal_state: {s.get("terminal_state","?")}')
for i, a in enumerate(s.get('attempts', [])):
    cr = a.get('compile_result', {})
    pa = a.get('patch_applied', {})
    rv = a.get('revert_result', {})
    cs = cr.get('status', 'none')
    ce = cr.get('error', '')[:80] if cr.get('error') else ''
    print(f'  attempt {i}: patch={"Y" if pa.get("files") else "N"} compile={cs}')
    if ce:
        print(f'    compile_err: {ce}')
    if rv:
        print(f'    revert: {rv.get("status","?")}')
EOF
# 预期：compile COMPILE_FAILED，revert REVERTED
```

- [ ] **Step 5: 确认 worktree 回滚后源码状态**

worktree 回滚的是 analyzer 产出的补丁（在 worktree 中），不是手动注入的编译错误（在主 workspace）。

```bash
cd ~/workspace/aosp/vendor/lechao
echo "=== 主 workspace 的故障注入状态 ==="
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/lechao_lcview.cpp || echo "0"
echo "=== 检查 worktree 残留 ==="
ls -d ~/workspace/aosp/vendor/lechao/.loop-worktrees/ 2>/dev/null && echo "worktree dir exists" || echo "no worktree dir (clean)"
git worktree list
```

- [ ] **Step 6: 手动清理编译错误（主 workspace 的故障）**

```bash
cd ~/workspace/aosp/vendor/lechao
git checkout -- services/lechao_lcview/daemon/lechao_lcview.cpp
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/lechao_lcview.cpp
# 预期：0
git status --short
# 预期：clean
```

---

## Task 10: 故障 F6 — init.rc 改动致 boot timeout（DD_BOOT_REBOOT 高风险）

**目标：** 在 init.rpi5.rc 追加无效 service，验证 DD_BOOT_REBOOT 全链路 + 四阶段防护网 + BOOT_TIMEOUT 检测。

> ⚠️ **高风险前置检查：**
> - SD 卡可物理拔出重刷
> - 串口始终可用（COM5 → 9700）
> - 已备份当前 boot.img

**验证能力清单：**
- [x] DD_BOOT_REBOOT 模式决策（.rc 改动 → boot.img 重建）
- [x] mk_rpi5_full_image.sh -mode 2 编译
- [x] dd + reboot 全链路
- [x] 四阶段防护网（镜像校验 / 设备健康 / 备份 / boot_completed）
- [x] BOOT_TIMEOUT → ESCALATE_HUMAN
- [x] 串口诊断 + serial_rollback_dd（若需回滚）

**Files:**
- Modify（临时）: `~/workspace/aosp/device/brcm/rpi5/ramdisk/init.rpi5.rc`

- [ ] **Step 1: 备份当前 boot.img（恢复用）**

```bash
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)
adb connect $DEV_IP:5555
adb root
mkdir -p /tmp/le-recovery
adb pull /dev/block/mmcblk0p1 /tmp/le-recovery/boot_pre_f6.img 2>/dev/null || \
  adb shell "dd if=/dev/block/mmcblk0p1 bs=4M" > /tmp/le-recovery/boot_pre_f6.img 2>/dev/null
ls -lh /tmp/le-recovery/boot_pre_f6.img
# 确认备份存在
```

- [ ] **Step 2: 注入 init.rc 无效 service**

```bash
cd ~/workspace/aosp/device/brcm/rpi5
# 确认 git 状态
git status --short ramdisk/init.rpi5.rc
# 追加无效 service（oneshot，不会 kernel panic）
cat >> ramdisk/init.rpi5.rc << 'EOF'

# FAULT-INJECTED: 无效 service（触发 DD_BOOT_REBOOT 决策验证）
service lechao_fault_test /system/bin/nonexistent_fault_binary
    class main
    user root
    oneshot
EOF
git diff --stat ramdisk/init.rpi5.rc
```

- [ ] **Step 3: 验证 decider 决策为 DD_BOOT_REBOOT**

```bash
cd ~/workspace/aosp
python3 << 'EOF'
import sys
sys.path.insert(0, '/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop/deploy/python')
from loop_deploy.decider import decide
plan = decide(['device/brcm/rpi5/ramdisk/init.rpi5.rc'])
print(f'mode={plan.mode.value}')
print(f'reason={plan.reason}')
print(f'requires_reboot={plan.requires_reboot}')
print(f'build_targets={plan.build_targets}')
assert plan.mode.value == 'DD_BOOT_REBOOT', f'expected DD_BOOT_REBOOT, got {plan.mode.value}'
print('PASS: decider correctly chose DD_BOOT_REBOOT')
EOF
```

- [ ] **Step 4: 检查 system.boot suite 是否存在**

```bash
ls engineering/loop/cases/system/ 2>/dev/null || echo "NO system suite"
ls engineering/loop/cases/features/system* 2>/dev/null || echo "NO system feature"
```

若不存在 system.boot suite，需创建一个最简 boot-success.yaml：
```yaml
# engineering/loop/cases/features/system/boot-success.yaml
name: system-boot-success
target: system.boot
cases:
  - id: boot_completed_check
    command: "getprop sys.boot_completed"
    expect: "1"
    timeout: 30
```

- [ ] **Step 5: 初始化 runtime session**

```bash
ARTIFACTS=engineering/output/runs/lcview-f6-ddboot-$(date +%Y%m%d%H%M%S)
mkdir -p $ARTIFACTS

bash engineering/loop/scripts/le.sh runtime init \
  --target system.boot \
  --suite engineering/loop/cases/features/system/boot-success.yaml \
  --max-attempts 2 \
  --artifacts-dir $ARTIFACTS
```

- [ ] **Step 6: 执行 runtime（DD_BOOT_REBOOT 全链路）**

```bash
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)
# device/brcm/rpi5 是 git 仓库，LE_PATCH_GIT_ROOT 指向它
export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/device/brcm/rpi5"

bash engineering/loop/scripts/le.sh runtime run \
  --session $ARTIFACTS/session.json \
  --adb-endpoint $DEV_IP:5555 2>&1 | tee $ARTIFACTS/runtime-run.log
```

**预期流程：**
1. RUN_VERIFY → FAIL（boot_completed check 或 service 状态）
2. analyzer 分析（可能无 KB/规则匹配 → OpencodeAnalyzer 或直接 ESCALATE）
3. 若 analyzer 产出补丁 → APPLY_PATCH（worktree）→ COMPILE（mode 2 重建 boot.img）→ DEPLOY（dd + reboot）
4. DEPLOY 四阶段防护网逐项检查
5. boot_completed 超时（120s）→ BOOT_TIMEOUT → ESCALATE_HUMAN

- [ ] **Step 7: 验证防护网触发**

```bash
python3 << EOF
import json
s = json.load(open('$ARTIFACTS/session.json'))
print(f'terminal_state: {s.get("terminal_state","?")}')
print(f'transition_reason: {s.get("transition_reason","")}')
for i, a in enumerate(s.get('attempts', [])):
    dr = a.get('deploy_result', {})
    print(f'  attempt {i}:')
    print(f'    deploy_status: {dr.get("status","?")}')
    print(f'    mode: {dr.get("mode","?")}')
    print(f'    error: {dr.get("error","")[:120]}')
    print(f'    backup_path: {dr.get("backup_path","")}')
EOF
```

- [ ] **Step 8: 串口验证设备状态**

```bash
python3 engineering/loop/scripts/rp5_serial_helper.py shell "getprop sys.boot_completed" --host 127.0.0.1 --port 9700
# 若返回 1：设备正常 boot（无效 service 不致命）
# 若无响应/超时：设备可能卡住，需串口回滚

python3 engineering/loop/scripts/rp5_serial_helper.py shell "dmesg | grep -i 'fault_test\|nonexistent'" --host 127.0.0.1 --port 9700
# 预期：service lechao_fault_test 启动失败的日志
```

- [ ] **Step 9: 设备恢复（若需要）**

**情况 A：设备正常 boot（无效 service 不致命）**
```bash
# init.rc 改动需要通过重新 dd 正常 boot.img 恢复
# 先清理 workspace 的 init.rc
cd ~/workspace/aosp/device/brcm/rpi5
git checkout -- ramdisk/init.rpi5.rc
# 重建 boot.img
cd ~/workspace/aosp
bash engineering/harness/scripts/mk_rpi5_full_image.sh -mode 2
# dd 正常 boot.img 回设备
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)
adb connect $DEV_IP:5555 && adb root
adb push out/target/product/rpi5/boot.img /data/local/tmp/boot.img
adb shell "dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M && sync"
adb reboot
```

**情况 B：设备卡住（串口可达）**
```bash
# 通过 serial_rollback_dd 回滚
python3 << 'EOF'
import sys
sys.path.insert(0, '/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop/deploy/python')
from loop_deploy.rollback import serial_rollback_dd
# 使用 Step 1 的备份
result = serial_rollback_dd(
    serial_shell=None,  # 需要传入 serial_shell callable
    backup_path="/tmp/le-recovery/boot_pre_f6.img",
    block_device="/dev/block/mmcblk0p1",
)
print(f'rollback result: {result.success} {result.reason}')
EOF
```

**情况 C：设备完全不可达（最后手段）**
```bash
# 物理拔 SD 卡，用 dd 或 balenaEtcher 重刷
# 备份在 /tmp/le-recovery/boot_pre_f6.img 或 Windows 侧的镜像
```

- [ ] **Step 10: 回滚 init.rc workspace 改动**

```bash
cd ~/workspace/aosp/device/brcm/rpi5
git checkout -- ramdisk/init.rpi5.rc
git status --short ramdisk/init.rpi5.rc
# 预期：clean
```

---

## Task 11: 收尾归档与文档同步

**目标：** 归档验证结果，确认知识库增长，同步文档，全量回归。

- [ ] **Step 1: 汇总各故障注入验证结果**

```bash
python3 << 'EOF'
import json, glob, os

results = []
for d in sorted(glob.glob('engineering/output/runs/lcview-f*-*/')):
    session_path = os.path.join(d, 'session.json')
    if not os.path.isfile(session_path):
        continue
    s = json.load(open(session_path))
    results.append({
        'dir': os.path.basename(d.rstrip('/')),
        'terminal_state': s.get('terminal_state', '?'),
        'attempts': len(s.get('attempts', [])),
        'target': s.get('target', '?'),
    })

print("=== 故障注入验证矩阵 ===")
for r in results:
    print(f"  {r['dir']}: state={r['terminal_state']} attempts={r['attempts']} target={r['target']}")
EOF
```

- [ ] **Step 2: 确认 KB 增长**

```bash
python3 -c "
import json
kb = json.load(open('engineering/loop/config/patch_knowledge_base.json'))
print(f'KB entries: {len(kb.get(\"entries\", []))}')
for e in kb.get('entries', []):
    print(f'  {e[\"fingerprint\"][:40]}... conf={e[\"confidence\"]} hits={e.get(\"hit_count\",0)}')
"
```

- [ ] **Step 3: 全量 pytest 回归**

```bash
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python:engineering/loop/deploy/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python" \
  python3 -m pytest engineering/loop/ --tb=short -v
# 预期：全部 PASS
```

- [ ] **Step 4: C++ 单元测试编译验证**

```bash
cd ~/workspace/aosp
source build/envsetup.sh && lunch aosp_rpi5-bp1a-userdebug
make lechao_lcview_unit_test lechao_lcview_hal_test -j$(nproc) 2>&1 | tail -5
# 预期：编译成功
```

- [ ] **Step 5: sync 脚本最终验证**

```bash
bash engineering/harness/workflows/lc-sync-code-to-patchs/sync_code_to_patchs.sh --check-only 2>&1 | grep -i "\.git" | head -3 || echo "NO .git LEAK"
# 预期：NO .git LEAK
```

- [ ] **Step 6: 更新文档（如需要）**

检查以下文档是否需要同步：
```bash
# controller README 的 analyzer 章节
grep -c "ScriptedAnalyzer\|lcview.*rule" engineering/loop/controller/README.md
# 若 Task 6 新增的规则未文档化，补充

# WORKFLOW.md 的故障注入章节
grep -c "fault.*inject\|故障注入" engineering/loop/WORKFLOW.md
```

- [ ] **Step 7: 最终 commit**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance
git add -A
git status --short
git commit -m "test(loop): lcview fault injection full capability validation

Validated 6 fault scenarios across all loop engineering capabilities:
- KB analyzer (0.98): daemon validate fault
- ScriptedAnalyzer (0.95): HAL connect fault + new lcview rule
- OpencodeAnalyzer (0.8): schema event_id drift, FileWriter naming
- COMPILE_FAILED -> REVERT_PATCH: compile error rollback
- DD_BOOT_REBOOT: init.rc change + 4-stage safety net

Fixed 3 framework gaps:
- vendor/lechao local git init for worktree isolation
- .git exclusion in sync/revert scripts
- worktree ws_root targeting via LE_PATCH_GIT_ROOT"
```

---

## 验证矩阵（预期最终结果）

| Task | 故障 | analyzer | deploy | 预期终态 | 验证能力 |
|------|------|----------|--------|---------|---------|
| T5 | daemon validate 日志 | KB 0.98 | PUSH_SINGLE | DONE_SUCCESS | KB fingerprint 匹配 + 归档 |
| T6 | HAL connect 日志 | Scripted 0.95 | PUSH_SINGLE | DONE_SUCCESS | 规则引擎 + 新增规则 |
| T7 | schema event_id | Opencode 0.8 | PUSH_SINGLE | DONE_SUCCESS (可能 human gate) | LLM subprocess + JSON 解析 |
| T8 | FileWriter 命名 | Opencode 0.8 | PUSH_SINGLE | DONE_SUCCESS | progress_converging guard |
| T9 | 编译错误 | 无 | COMPILE_FAILED | ESCALATE_HUMAN | worktree 回滚 |
| T10 | init.rc 改动 | 无 | DD_BOOT_REBOOT | ESCALATE_HUMAN (BOOT_TIMEOUT) | 四阶段防护网 + 串口 |
