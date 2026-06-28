# Plan B: 低中风险故障注入（Task 5-8）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 注入 4 类低中风险故障，验证三层 analyzer（KB / Scripted / Opencode）+ progress_converging guard + PUSH_SINGLE 部署闭环。

**Spec:** `docs/specs/2026-06-28-lcview-fault-injection-loop-validation-design.md` §3.2 F1-F4

**前置：** Plan A（Task 1-4）全部完成，基线 PASS。

**通用约定：**
- 每个 Task 先注入故障到 `~/workspace/aosp/vendor/lechao`（已 git 化）
- 执行 `le runtime init → run`，由框架自动驱动 verify → analyze → patch → compile → deploy 闭环
- Task 结束后确认源码已恢复（runtime 自动修复或手动 `git checkout`）

---

## Task 5: 故障 F1 — Daemon validate failed 日志（KB 闭环）

**目标：** 验证 KnowledgeBaseAnalyzer（confidence=0.98）的 fingerprint 匹配 + DONE_SUCCESS 自动归档。

**验证能力清单：**
- [x] KB fingerprint 匹配（已有 bad magic 记录）
- [x] PUSH_SINGLE 部署链路（mmm 编译 → adb push → restart service）
- [x] DONE_SUCCESS 时 hit_count 累加
- [x] 第二次 RUN_VERIFY PASS 收敛

**Files:**
- Modify（临时）: `~/workspace/aosp/vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp`

- [ ] **Step 1: 确认 KB 已有 bad magic 记录**

```bash
python3 -c "
import json
kb = json.load(open('engineering/loop/config/patch_knowledge_base.json'))
for e in kb.get('entries', []):
    fp = e.get('fingerprint', '')
    if 'bad magic' in fp or 'validate' in fp.lower():
        print(f'  fingerprint: {fp}')
        print(f'  confidence: {e.get(\"confidence\")}')
        print(f'  hit_count: {e.get(\"hit_count\", 0)}')
"
# 预期：找到 bad magic 记录
```

若 KB 无此记录，需先手动构造或跳过 KB 层直接验证 Scripted 层。

- [ ] **Step 2: 注入故障（daemon main loop 入口插入 bad magic 日志）**

```bash
cd ~/workspace/aosp/vendor/lechao
python3 << 'EOF'
p = 'services/lechao_lcview/daemon/lechao_lcview.cpp'
c = open(p).read()
# 定位 main loop 入口的 ALOGI
marker = 'ALOGI("lechao_lcview: starting");'
if marker not in c:
    # 尝试其他可能的入口标记
    print("WARNING: marker not found, dumping main() area")
    import re
    m = re.search(r'int main\(.*?\{.*?\n', c, re.DOTALL)
    if m:
        print(repr(m.group()[:500]))
    raise SystemExit(1)
fault_line = marker + '\n    // FAULT-INJECTED: validate 故障\n    ALOGE("lechao_lcview: parse: validate failed: bad magic (fault injected)");'
assert c.count(marker) == 1, f'marker count={c.count(marker)}'
open(p, 'w').write(c.replace(marker, fault_line, 1))
print('fault F1 injected')
EOF
git diff --stat
```

- [ ] **Step 3: 初始化 runtime session**

```bash
ARTIFACTS=engineering/output/runs/lcview-f1-kb-$(date +%Y%m%d%H%M%S)
mkdir -p $ARTIFACTS
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)

bash engineering/loop/scripts/le.sh runtime init \
  --target lcview \
  --suite engineering/loop/cases/features/lcview/common.yaml \
  --max-attempts 3 \
  --artifacts-dir $ARTIFACTS
# 记录输出的 session_path
```

- [ ] **Step 4: 执行 runtime 自动闭环**

```bash
export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
bash engineering/loop/scripts/le.sh runtime run \
  --session $ARTIFACTS/session.json \
  --adb-endpoint $DEV_IP:5555 2>&1 | tee $ARTIFACTS/runtime-run.log
```

- [ ] **Step 5: 验证收敛结果**

```bash
python3 << EOF
import json
s = json.load(open('$ARTIFACTS/session.json'))
print(f'terminal_state: {s.get(\"terminal_state\",\"?\")}')
print(f'attempts: {len(s.get(\"attempts\",[]))}')
for i, a in enumerate(s.get('attempts', [])):
    v = a.get('verify_result', '?')
    fc = a.get('failed_count', '?')
    pa = a.get('patch_applied', {}).get('patch_hash', '')[:12]
    print(f'  attempt {i}: verify={v} failed={fc} patch={pa}')
EOF
# 预期：terminal_state=DONE_SUCCESS
# attempt 0: verify=FAIL（发现 bad magic 日志）
# attempt 1: verify=PASS（KB 修复后）
```

- [ ] **Step 6: 验证 KB 命中**

```bash
cat $ARTIFACTS/patch_suggestion.json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(f'rationale: {d.get(\"rationale\",\"\")}')
    print(f'confidence: {d.get(\"confidence\",0)}')
except: print('no patch_suggestion.json (KB may use inline)')
" || echo "no patch_suggestion"
# 预期：rationale 含 KnowledgeBaseAnalyzer，confidence=0.98
```

- [ ] **Step 7: 确认源码已恢复**

```bash
cd ~/workspace/aosp/vendor/lechao
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/lechao_lcview.cpp
# 预期：0（runtime 自动修复删除了故障行）
git log --oneline -3
# worktree 分支应已清理
```

- [ ] **Step 8: 若未自动恢复则手动清理**

```bash
cd ~/workspace/aosp/vendor/lechao
git checkout -- services/lechao_lcview/daemon/lechao_lcview.cpp
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/lechao_lcview.cpp
# 预期：0
```

---

## Task 6: 故障 F2 — HAL connect 失败（ScriptedAnalyzer 规则闭环）

**目标：** 新增确定性 ScriptedAnalyzer 规则，验证规则匹配（0.95）→ PUSH_SINGLE → 收敛。

**验证能力清单：**
- [x] ScriptedAnalyzer 规则引擎
- [x] 新增 `_rule_lcview_hal_connect_fault` 规则
- [x] 规则匹配产出补丁（删除注入日志行）
- [x] TDD：先写测试再实现

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py`
- Create: `engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py`
- Modify（临时）: `~/workspace/aosp/vendor/lechao/services/lechao_lcview/hal/LcView.cpp`

- [ ] **Step 1: 编写规则测试（TDD - 先写失败测试）**

创建 `engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py`：

```python
"""lcview 专属 ScriptedAnalyzer 规则测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop_controller.analyzer_protocol import _rule_lcview_hal_connect_fault


def test_hal_connect_fault_match_connect_failed():
    """failure_reason 含 'connect failed' 且涉及 lcview_hal 时命中。"""
    case = {
        "failure_reason": "lcview_hal_service_state: HAL connect failed: cannot cast to ILcView",
        "command": "getprop init.svc.lechao_lcview_hal",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None, "should match"
    assert len(result) == 1
    assert "LcView.cpp" in result[0].workspace_path


def test_hal_connect_fault_match_cannot_cast():
    """failure_reason 含 'cannot cast to ILcView' 时命中。"""
    case = {
        "failure_reason": "HAL daemon reports: cannot cast to ILcView",
        "command": "getprop init.svc.lechao_lcview_hal",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None


def test_hal_connect_fault_no_match_unrelated():
    """无关 failure_reason 不命中。"""
    case = {"failure_reason": "some unrelated error", "command": "getprop"}
    assert _rule_lcview_hal_connect_fault(case) is None


def test_hal_connect_fault_no_match_no_lcview():
    """不含 lcview 关键词的不命中。"""
    case = {"failure_reason": "connect failed somewhere", "command": "getprop init.svc.other"}
    assert _rule_lcview_hal_connect_fault(case) is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH="engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py -v
# 预期：ImportError（_rule_lcview_hal_connect_fault 不存在）
```

- [ ] **Step 3: 实现规则（analyzer_protocol.py）**

先确认现有规则的位置和格式：
```bash
grep -n "_RULES\|def _rule_" engineering/loop/controller/python/loop_controller/analyzer_protocol.py | head -20
```

在现有规则定义之后、`_RULES` 列表之前，新增：

```python
_LCVIEW_HAL_PATH = "vendor/lechao/services/lechao_lcview/hal/LcView.cpp"


def _rule_lcview_hal_connect_fault(case: dict) -> list[FileChange] | None:
    """LCVIEW HAL connect 故障：日志含 connect failed / cannot cast to ILcView。

    触发条件：failure_reason 含 "connect failed" 或 "cannot cast to ILcView"，
    且涉及 lechao_lcview_hal 服务。
    修复动作：删除注入的故障日志行。
    confidence: 0.95（确定性规则）
    """
    reason = (case.get("failure_reason") or "").lower()
    command = (case.get("command") or "").lower()
    # 必须涉及 lcview_hal
    if "lechao_lcview_hal" not in command and "lcview" not in reason:
        return None
    # 必须含 connect 故障特征
    if "connect failed" not in reason and "cannot cast to ilcview" not in reason:
        return None
    return [FileChange(
        workspace_path=_LCVIEW_HAL_PATH,
        change_type="edit",
        old_marker='    // FAULT-INJECTED: HAL connect 故障\n    ALOGE("LcView: connect failed: cannot cast to ILcView (fault injected)");\n',
        new_content='',
    )]
```

在 `_RULES` 列表追加此规则函数。

- [ ] **Step 4: 运行测试确认通过**

```bash
PYTHONPATH="engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py -v
# 预期：4 passed
```

- [ ] **Step 5: 注入 HAL 故障**

先读取 LcView.cpp 确认可注入的锚点：
```bash
grep -n "ALOGE\|cannot cast\|bind_hal\|connect" ~/workspace/aosp/vendor/lechao/services/lechao_lcview/hal/LcView.cpp | head -10
```

注入故障（在 daemon main 中调用 HAL 处插入）：
```bash
cd ~/workspace/aosp/vendor/lechao
python3 << 'EOF'
p = 'services/lechao_lcview/daemon/lechao_lcview.cpp'
c = open(p).read()
# 在 daemon 获取 HAL proxy 后注入故障日志
marker = 'ALOGI("lechao_lcview: starting");'
if marker not in c:
    raise SystemExit("marker not found, check LcView.cpp content")
fault = marker + '\n    // FAULT-INJECTED: HAL connect 故障\n    ALOGE("LcView: connect failed: cannot cast to ILcView (fault injected)");'
assert c.count(marker) == 1
open(p, 'w').write(c.replace(marker, fault, 1))
print('HAL fault F2 injected')
EOF
git diff --stat
```

注意：规则中的 `old_marker` 必须与注入的文本完全一致（含缩进和换行）。

- [ ] **Step 6: runtime 自动闭环**

```bash
ARTIFACTS=engineering/output/runs/lcview-f2-rule-$(date +%Y%m%d%H%M%S)
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

- [ ] **Step 7: 验证 ScriptedAnalyzer 命中**

```bash
cat $ARTIFACTS/patch_suggestion.json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
r = d.get('rationale','')
c = d.get('confidence',0)
print(f'rationale: {r}')
print(f'confidence: {c}')
assert 'ScriptedAnalyzer' in r or 'rule' in r.lower(), 'not scripted analyzer'
assert c >= 0.9, 'confidence too low for scripted'
print('PASS: ScriptedAnalyzer rule matched')
" || echo "check patch_suggestion manually"
```

- [ ] **Step 8: 确认源码恢复 + 手动清理兜底**

```bash
cd ~/workspace/aosp/vendor/lechao
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/lechao_lcview.cpp || true
git checkout -- services/lechao_lcview/daemon/lechao_lcview.cpp 2>/dev/null || true
```

- [ ] **Step 9: Commit 规则代码**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py \
        engineering/loop/controller/python/tests/test_lcview_analyzer_rules.py
git commit -m "feat(loop): add lcview HAL connect fault analyzer rule

ScriptedAnalyzer rule _rule_lcview_hal_connect_fault for LcView.cpp
connect/cast failures. Triggered by 'connect failed' or 'cannot cast
to ILcView' in lcview_hal related case failure_reasons. confidence=0.95."
```

---

## Task 7: 故障 F3 — Schema event_id 偏移（OpencodeAnalyzer LLM 闭环）

**目标：** 修改 lcview_events.json event_id（4→14），使内核事件无法匹配 schema → jsonl 不生成。KB 和规则均无匹配 → OpencodeAnalyzer（LLM）生成修复。

**验证能力清单：**
- [x] OpencodeAnalyzer subprocess 调用（`opencode run --format json`）
- [x] LLM 分析 evidence 生成 JSON 补丁
- [x] confidence=0.8 + 可能触发 human gate
- [x] end_to_end suite（含 jsonl 文件检查）

**Files:**
- Modify（临时）: `~/workspace/aosp/vendor/lechao/services/lechao_lcview/config/lcview_events.json`

- [ ] **Step 1: 确认 opencode CLI 可用**

```bash
which opencode && opencode --version
# 预期：opencode 版本号
```

- [ ] **Step 2: 确认 analyzer.yaml 配置了 opencode**

```bash
cat engineering/loop/config/analyzer.yaml
# 预期：opencode 段含 model/timeout/binary 配置
```

- [ ] **Step 3: 注入 schema event_id 偏移**

```bash
cd ~/workspace/aosp/vendor/lechao
python3 << 'EOF'
import json
p = 'services/lechao_lcview/config/lcview_events.json'
d = json.load(open(p))
orig_id = d['events'][0]['id']
d['events'][0]['id'] = 14  # 内核写 4，schema 期望 14 → validate 失败
json.dump(d, open(p, 'w'), indent=2, ensure_ascii=False)
print(f'schema fault F3 injected: {orig_id} -> 14')
EOF
git diff --stat
```

- [ ] **Step 4: runtime 自动闭环（end_to_end suite）**

```bash
ARTIFACTS=engineering/output/runs/lcview-f3-llm-$(date +%Y%m%d%H%M%S)
mkdir -p $ARTIFACTS
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)

bash engineering/loop/scripts/le.sh runtime init \
  --target lcview \
  --suite engineering/loop/cases/features/lcview/end_to_end.yaml \
  --max-attempts 3 \
  --artifacts-dir $ARTIFACTS

export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
bash engineering/loop/scripts/le.sh runtime run \
  --session $ARTIFACTS/session.json \
  --adb-endpoint $DEV_IP:5555 2>&1 | tee $ARTIFACTS/runtime-run.log
```

- [ ] **Step 5: 若触发 human gate 则 approve**

```bash
python3 -c "
import json
s = json.load(open('$ARTIFACTS/session.json'))
gate = s.get('pending_human_gate', False)
print(f'pending_human_gate: {gate}')
if gate:
    print('patch path:', '$ARTIFACTS/patch_suggestion.json')
"

# 检查 LLM 产出的补丁
cat $ARTIFACTS/patch_suggestion.json | python3 -m json.tool

# 若 approve：
# bash engineering/loop/scripts/le.sh runtime approve --session $ARTIFACTS/session.json
```

- [ ] **Step 6: 验证 OpencodeAnalyzer 被调用**

```bash
cat $ARTIFACTS/patch_suggestion.json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
r = d.get('rationale', '')
c = d.get('confidence', 0)
print(f'rationale: {r}')
print(f'confidence: {c}')
if 'OpencodeAnalyzer' in r or 'opencode' in r.lower() or 'llm' in r.lower():
    print('PASS: OpencodeAnalyzer was invoked')
else:
    print('WARNING: may not be OpencodeAnalyzer')
"
```

- [ ] **Step 7: 验证 event_id 被修复**

```bash
cd ~/workspace/aosp/vendor/lechao
python3 -c "
import json
d = json.load(open('services/lechao_lcview/config/lcview_events.json'))
eid = d['events'][0]['id']
print(f'first event id: {eid}')
assert eid == 4, f'expected 4, got {eid}'
print('PASS: event_id restored to 4')
"
```

- [ ] **Step 8: 手动清理兜底**

```bash
cd ~/workspace/aosp/vendor/lechao
git checkout -- services/lechao_lcview/config/lcview_events.json 2>/dev/null || true
```

---

## Task 8: 故障 F4 — FileWriter 命名规则破坏（LLM + 收敛 guard）

**目标：** 破坏 jsonl 文件命名规则（`{event_id}_{name}_{date}_p{seq}.jsonl`），触发 filename_rule 用例失败 + progress_converging guard。

**验证能力清单：**
- [x] progress_converging guard（失败用例数下降时宽限 RETRY）
- [x] OpencodeAnalyzer 对 C++ 代码的修复能力
- [x] end_to_end suite 的 jsonl 文件名检查

**Files:**
- Modify（临时）: `~/workspace/aosp/vendor/lechao/services/lechao_lcview/daemon/FileWriter.cpp`

- [ ] **Step 1: 确认 FileWriter.cpp 的 makeFilename 方法**

```bash
grep -n "makeFilename\|schema.name\|jsonl" ~/workspace/aosp/vendor/lechao/services/lechao_lcview/daemon/FileWriter.cpp | head -10
```

- [ ] **Step 2: 注入命名规则破坏**

```bash
cd ~/workspace/aosp/vendor/lechao
python3 << 'EOF'
p = 'services/lechao_lcview/daemon/FileWriter.cpp'
c = open(p).read()
# makeFilename 中的 schema.name 替换为固定字符串
orig = 'oss << mCfg.logDir << "/" << schema.id << "_" << schema.name'
if orig not in c:
    # 尝试找到实际的 makeFilename 实现
    import re
    m = re.search(r'(oss << mCfg\.logDir << "/" << schema\.id << "_" << )(\w+)', c)
    if m:
        print(f'found pattern: {m.group()}')
        orig = m.group(1) + m.group(2)
    else:
        raise SystemExit("makeFilename pattern not found")
fault_marker = '// FAULT-INJECTED: 命名规则破坏'
if fault_marker in c:
    raise SystemExit("fault already injected")
# 在 schema.name 前插入 fault 注释 + 替换
fault = 'oss << mCfg.logDir << "/" << schema.id << "_unknown_fault"  // FAULT-INJECTED: 命名规则破坏'
assert c.count(orig) == 1, f'orig count={c.count(orig)}'
open(p, 'w').write(c.replace(orig, fault, 1))
print('FileWriter naming fault F4 injected')
EOF
git diff --stat
```

- [ ] **Step 3: runtime 自动闭环**

```bash
ARTIFACTS=engineering/output/runs/lcview-f4-naming-$(date +%Y%m%d%H%M%S)
mkdir -p $ARTIFACTS
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)

bash engineering/loop/scripts/le.sh runtime init \
  --target lcview \
  --suite engineering/loop/cases/features/lcview/end_to_end.yaml \
  --max-attempts 4 \
  --artifacts-dir $ARTIFACTS

export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
bash engineering/loop/scripts/le.sh runtime run \
  --session $ARTIFACTS/session.json \
  --adb-endpoint $DEV_IP:5555 2>&1 | tee $ARTIFACTS/runtime-run.log
```

- [ ] **Step 4: 验证收敛 + 分析 attempt 历史**

```bash
python3 << EOF
import json
s = json.load(open('$ARTIFACTS/session.json'))
print(f'terminal_state: {s.get(\"terminal_state\",\"?\")}')
for i, a in enumerate(s.get('attempts', [])):
    fc = a.get('failed_count', '?')
    v = a.get('verify_result', '?')
    print(f'  attempt {i}: verify={v} failed_count={fc}')
# 检查 progress_converging（failed_count 应下降）
EOF
```

- [ ] **Step 5: 手动清理兜底**

```bash
cd ~/workspace/aosp/vendor/lechao
git checkout -- services/lechao_lcview/daemon/FileWriter.cpp 2>/dev/null || true
grep -c "FAULT-INJECTED" services/lechao_lcview/daemon/FileWriter.cpp
# 预期：0
```
