# `/le` zygote 诊断与候选补丁草案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/le` 的第 4-5 步补齐为可 live 验证的首版闭环：`le run` FAIL 后自动进入诊断，围绕 zygote 未正常起来这一类症状生成克制的诊断报告与 `~/workspace/` 候选补丁草案。

**Architecture:** 保持 `loop_core` 继续专注于确定性执行与 EvidenceBundle 输出，不在 `engineering/loop` 内新增 analyzer。诊断与补丁草案生成放在 `/le` 后半段，通过 `.opencode/commands/le.md`、`engineering/loop/WORKFLOW.md`、`engineering/harness/templates/diagnosis-report-template.md` 编码 fail-path 契约；同时补强 `boot-success.yaml`，确保 `trigger_reboot` 早期失败时也能采到足够证据。

**Tech Stack:** Markdown 工作流契约、YAML case 定义、Python pytest（文档契约测试 + YAML 解析测试）、bash 校验脚本、OpenCode slash command。

**Spec:** `docs/specs/2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md`

---

## File Structure

- Modify: `.opencode/commands/le.md`
  - `/le` 的用户入口契约。需要明确：FAIL 后进入诊断、可选询问用户线索、生成诊断报告与候选补丁草案、禁止强行唯一根因。
- Modify: `engineering/loop/WORKFLOW.md`
  - 作为 loop 权威工作流文档，更新步骤 4-5 的语义、诊断输入白名单、报告章节、失败保护策略。
- Modify: `engineering/harness/templates/diagnosis-report-template.md`
  - 把旧的“根因假设 / 根因分析”模板升级为“结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / case 建议 / 循环终止建议”。
- Modify: `engineering/harness/templates/README.md`
  - 同步模板说明，避免模板 README 与模板内容脱节。
- Modify: `engineering/loop/README.md`
  - 用户文档中补上 `/le` 失败后诊断、用户线索、报告落盘位置的说明。
- Modify: `engineering/loop/cases/system/boot-success.yaml`
  - 给 `trigger_reboot` 增加 `on_fail.collectors`，确保早期失败时也能采到 `serial_recent / init_log / crash_dump / kmsg`。
- Create: `engineering/loop/core/python/tests/test_diagnosis_contract_docs.py`
  - 锁定诊断模板、WORKFLOW、`/le` 命令说明的关键契约，防止未来回退成“强根因 / 无线索 / 无补丁草案”的旧语义。
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
  - 补一个回归测试，确保 `trigger_reboot` 真的带上早期失败 collector。

---

### Task 1: 锁定 `/le` 失败后诊断契约

**Files:**
- Create: `engineering/loop/core/python/tests/test_diagnosis_contract_docs.py`
- Modify: `engineering/harness/templates/diagnosis-report-template.md`
- Modify: `engineering/harness/templates/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `.opencode/commands/le.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 写失败测试，锁定新诊断契约**

创建 `engineering/loop/core/python/tests/test_diagnosis_contract_docs.py`：

```python
from pathlib import Path


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while path.name != "engineering":
        if path == path.parent:
            raise RuntimeError("engineering/ root not found")
        path = path.parent
    return path.parent


def _read(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def test_diagnosis_template_uses_fact_based_sections() -> None:
    text = _read("engineering/harness/templates/diagnosis-report-template.md")

    assert "## 3. 现象归类与不确定性" in text
    assert "## 4. 调查线索（用户提供，未验证）" in text
    assert "## 5. 候选修复方向（人工执行）" in text
    assert "不强行下唯一根因结论" in text
    assert "根因假设" not in text
    assert "## 3. 根因分析" not in text



def test_workflow_documents_fail_path_and_optional_user_clues() -> None:
    text = _read("engineering/loop/WORKFLOW.md")

    assert "有 fail → AI 读 EvidenceBundle 分析证据并收敛候选修复方向" in text
    assert "AI 生成候选补丁草案（人工确认后再实施）" in text
    assert "任何 FAIL 都进入诊断阶段" in text
    assert "调查线索（用户提供，未验证）" in text
    assert "不强行给唯一根因" in text



def test_le_command_describes_diagnosis_and_patch_draft_flow() -> None:
    text = _read(".opencode/commands/le.md")

    assert "诊断报告" in text
    assert "候选补丁草案" in text
    assert "人工确认" in text
    assert "用户线索" in text
```

- [ ] **Step 2: 运行测试，确认它们先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_diagnosis_contract_docs.py -v
```

Expected: FAIL，至少出现以下断言失败之一：
- 找不到 `## 3. 现象归类与不确定性`
- `WORKFLOW.md` 仍包含“根因分析 / 修改代码”的旧语义
- `.opencode/commands/le.md` 未提到“用户线索”或“候选补丁草案”

- [ ] **Step 3: 更新诊断报告模板，去掉强根因表述**

把 `engineering/harness/templates/diagnosis-report-template.md` 的“报告结构”和“AI 行为约束”改成下面的版本：

```markdown
# Boot 诊断报告模板

> 本模板约束 AI（opencode）在收到 EvidenceBundle 后产出的诊断报告格式。
> 报告路径：与本次 `evidence_bundle.json` 同目录，文件名固定为 `diagnosis-report.md`。

## 报告结构

每份诊断报告必须包含以下 7 节，顺序固定：

1. **结论**
   - 整体状态：PASS / FAIL
   - 是否命中“zygote 未正常进入稳定 running 状态”这一类症状
   - 当前是否建议进入源码试探性修复

2. **证据链**
   - suite / case 结果
   - reboot transcript / serial snippet
   - init / service 状态
   - crash / tombstone
   - kmsg 等辅助信号

3. **现象归类与不确定性**
   - 只区分“确定事实 / 相关异常现象 / 当前不确定点”
   - 不强行下唯一根因结论

4. **调查线索（用户提供，未验证）**
   - 最近改动模块
   - suspect 范围
   - 首次出现版本 / 构建
   - 其他备注

5. **候选修复方向（人工执行）**
   - 每个方向都要包含：支撑证据 / 不确定点 / 目标源码范围 / 候选 diff / 风险说明 / 验证命令

6. **建议新增 / 调整 case**
   - 只给建议，不自动修改 YAML

7. **循环终止建议**
   - 是否建议人工 review
   - 是否建议进入下一轮改码 / 编译 / 重测
   - 若证据不足，明确写“不建议直接改码”

## AI 行为约束

1. AI 必须按此模板产出诊断报告，不得改成自由格式
2. 报告路径必须与本次 `evidence_bundle.json` 同目录
3. 第 5 节修复方向必须具体到 `~/workspace/` 文件路径和函数 / rc stanza / sepolicy rule / service 定义位置
4. 第 5 节的候选 diff 必须标注为“候选”，不得伪装成已验证正确修复
5. 第 6 节的 YAML 建议不自动应用，只给人工 review
6. 用户线索必须标记为“用户提供，未验证”，不得当成客观事实
7. 报告必须区分“确定事实 / 相关异常现象 / 当前不确定点”，不强行下唯一根因结论
```

- [ ] **Step 4: 更新 `WORKFLOW.md`，把第 4-5 步改成候选修复方向语义**

把 `engineering/loop/WORKFLOW.md` 的核心流程和“AI 诊断报告约束”更新成下面的文本：

```markdown
## 核心流程

1. AI 读代码/spec + template → 生成 YAML 用例
2. le run 执行用例 → EvidenceBundle JSON
3. 全 pass → 功能 OK
4. 有 fail → AI 读 EvidenceBundle 分析证据并收敛候选修复方向
5. AI 生成候选补丁草案（人工确认后再实施）
6. 编译部署（binary 自动 / 镜像确认）
7. goto 2，直到全 pass 或 N=5 回退人工

## AI 诊断报告约束（`/le` 第 4-5 步首版）

当 AI（opencode）通过 `/le` 触发诊断闭环并收到 EvidenceBundle 后，必须遵守以下规则：

1. 任何 FAIL 都进入诊断阶段
2. 诊断阶段只读取本次 run 的 `summary.txt`、`evidence_bundle.json`、bundle 引用的 artifacts，以及 `serial_context`
3. 诊断前可选询问一次调查线索（最近改动模块、suspect 范围、首次坏版本等）
4. 调查线索必须标记为“用户提供，未验证”，不得覆盖客观证据
5. 报告文件固定写到与本次 `evidence_bundle.json` 同目录的 `diagnosis-report.md`
6. 报告必须包含 7 节：结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / 建议新增调整 case / 循环终止建议
7. 不强行给唯一根因；允许并列多个候选修复方向
8. 只有当证据足以落到 `~/workspace/` 可操作范围时，才输出候选补丁草案；否则只出诊断报告
9. AI 不自动修改 `boot-success.yaml`
```

- [ ] **Step 5: 更新 `/le` 入口文案和 loop README**

把 `.opencode/commands/le.md` 改成：

```markdown
---
description: AI 驱动的设备验收闭环：执行用例 → 失败后分析证据 → 生成诊断报告与候选补丁草案 → 人工确认后继续重测
---
按 $ARGUMENTS 编排 loop 闭环（执行 / 诊断 / 候选补丁草案 / 重测建议；支持可选用户线索；不强行给唯一根因）：
@engineering/loop/WORKFLOW.md
```

在 `engineering/loop/README.md` 的 `EvidenceBundle 串口上下文` 章节后新增一节：

```markdown
## `/le` 失败后诊断

当 `/le` 驱动 `le run` 得到 FAIL 时，opencode 会读取本次 run 的 `summary.txt`、`evidence_bundle.json`、`serial_context` 与关联 collector 产物，先可选询问一次调查线索（如 suspect 模块、最近改动范围、首次坏版本），再在与本次 `evidence_bundle.json` 同目录下生成 `diagnosis-report.md`。

诊断报告只输出“确定事实 / 现象归类 / 当前不确定点 / 候选修复方向”，不强行给唯一根因。只有当证据足以指向 `~/workspace/` 的可操作范围时，才会给出候选补丁草案。
```

同时把 `engineering/harness/templates/README.md` 中 `diagnosis-report-template.md` 这一行改成：

```markdown
| [diagnosis-report-template.md](./diagnosis-report-template.md) | Loop boot 诊断报告模板，约束 AI 在 FAIL 后基于 EvidenceBundle 产出结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / case 建议 / 循环终止建议 | Loop boot 诊断报告产出 |
```

- [ ] **Step 6: 跑文档契约测试和 README 校验，确认全部通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_diagnosis_contract_docs.py -v && \
  bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected:
- `test_diagnosis_contract_docs.py` 全部 PASS
- `validate_harness_docs.sh` PASS，且不再提示 templates README 与模板内容不一致

- [ ] **Step 7: 提交本任务改动**

```bash
git add .opencode/commands/le.md \
        engineering/loop/WORKFLOW.md \
        engineering/loop/README.md \
        engineering/harness/templates/diagnosis-report-template.md \
        engineering/harness/templates/README.md \
        engineering/loop/core/python/tests/test_diagnosis_contract_docs.py
git commit -m "feat(loop): 规范 /le 失败后诊断与候选补丁草案契约"
```

---

### Task 2: 补强 `trigger_reboot` 的早期失败证据覆盖

**Files:**
- Modify: `engineering/loop/cases/system/boot-success.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试，锁定 `trigger_reboot` 的 on-fail collectors**

在 `engineering/loop/core/python/tests/test_case_loader.py` 末尾追加：

```python
def test_boot_success_trigger_reboot_has_early_failure_collectors():
    """trigger_reboot 失败时也会主动采集早期 boot 诊断证据。"""
    from pathlib import Path
    from loop_core.case_loader import load_suite

    repo_root = Path(__file__).resolve()
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            raise RuntimeError("engineering/ root not found")
    cases_dir = repo_root / "loop" / "cases"
    boot_yaml = cases_dir / "system" / "boot-success.yaml"

    suite = load_suite(str(boot_yaml), [str(cases_dir)])
    trigger_reboot = next(case for case in suite.cases if case.id == "trigger_reboot")

    assert trigger_reboot.on_fail["collectors"] == [
        "common.shell.serial_recent",
        "common.shell.init_log",
        "common.shell.crash_dump",
        "common.shell.kmsg",
    ]
```

- [ ] **Step 2: 跑测试，确认当前 YAML 先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_boot_success_trigger_reboot_has_early_failure_collectors -v
```

Expected: FAIL，`trigger_reboot.on_fail` 为空或缺少 `serial_recent / init_log / crash_dump / kmsg`

- [ ] **Step 3: 更新 `boot-success.yaml`，给 `trigger_reboot` 增加 on-fail collectors**

把 `engineering/loop/cases/system/boot-success.yaml` 的 `trigger_reboot` 改成：

```yaml
  - id: trigger_reboot
    action: reboot
    description: "触发设备重启并等待启动完成"
    severity: critical
    assert: {}
    on_fail:
      collectors: [serial_recent, init_log, crash_dump, kmsg]
```

文件顶部注释同步补充一句，说明：`trigger_reboot` 自己负责覆盖 reboot 早期失败证据，下游 case 即使被 skip，也不会丢失串口与内核现场。

- [ ] **Step 4: 跑 YAML 加载测试，确认 collector FQN 解析正确**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v
```

Expected:
- 新增测试 PASS
- 现有 `test_boot_success_yaml_has_trigger_reboot_first`、`test_common_shell_yaml_has_kmsg_collector` 继续 PASS

- [ ] **Step 5: 跑回归测试，确认执行器与 provider 测试不受影响**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  -v --import-mode=importlib
```

Expected:
- `test_executor_action_case_calls_reboot_and_wait` PASS
- `test_executor_action_case_fail_includes_stage_in_reason` PASS
- `test_rp5_transport_reboot_and_wait_*` 系列 PASS

- [ ] **Step 6: 提交本任务改动**

```bash
git add engineering/loop/cases/system/boot-success.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop): 补强 trigger_reboot 早期失败证据采集"
```

---

### Task 3: 端到端验收与人工可读性检查

**Files:**
- Modify: none（验证任务）

- [ ] **Step 1: 跑完整自动化回归，确认文档契约与 YAML 改动同时通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib && \
  bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected:
- Python tests 全绿
- `validate_harness_docs.sh` PASS

- [ ] **Step 2: 用历史 FAIL run 目录做一次干跑审阅**

在 OpenCode 会话里读取以下目录中的产物，人工检查报告契约是否能被 `/le` 正确执行：

- `engineering/output/runs/boot-success-live-20260620-205743/summary.txt`
- `engineering/output/runs/boot-success-live-20260620-205743/evidence_bundle.json`

检查点：
- 报告结构是否已经明确要求 7 节
- 是否明确区分“确定事实 / 现象归类 / 当前不确定点”
- 是否允许“只出报告，不出补丁草案”
- 是否要求把用户线索标记为“用户提供，未验证”

Expected: 所有检查点都能在 `WORKFLOW.md`、模板、`/le` 命令说明中直接找到对应文字约束

- [ ] **Step 3: 做一次 live 验收演练**

在 OpenCode 会话里执行：

```text
/le run --suite engineering/loop/cases/system/boot-success.yaml --host 127.0.0.1 --port 9700 --device-profile engineering/loop/connection/profiles/devices/rp5/default.json --case-dirs engineering/loop/cases --artifacts-dir engineering/output/runs/boot-success-live-diagnosis
```

Expected:
- 如果 suite PASS，流程正常结束
- 如果 suite FAIL，助手会先可选询问一次调查线索（如 suspect 模块、最近改动范围、首次坏版本）
- 随后在本次 `evidence_bundle.json` 同目录生成 `diagnosis-report.md`
- 报告不强行下唯一根因；若证据不足，可明确拒绝输出候选补丁草案

- [ ] **Step 4: 审查 live 报告产物，确认满足首版验收项**

用 Read 工具检查本次 run 目录下的 `diagnosis-report.md`，确认以下条目全部成立：

- 报告路径与 `evidence_bundle.json` 同目录
- 报告包含 7 节
- 至少引用 `trigger_reboot` / `serial_context` / 某个 collector 的客观证据
- 若给出候选补丁草案，落到 `~/workspace/` 具体源码位置并附风险与验证命令
- 若未给出草案，说明拒绝原因

Expected: 以上 5 项全部满足

---

## Self-Review Checklist

- [ ] **Spec coverage:** 对照 `docs/specs/2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md`，确认以下要求都各有任务覆盖：
  - `/le` FAIL 后自动诊断 → Task 1
  - 禁止强行唯一根因 → Task 1
  - 支持用户线索输入 → Task 1
  - `trigger_reboot` 早期失败证据覆盖 → Task 2
  - live 端到端验收 → Task 3
- [ ] **Placeholder scan:** 搜索本 plan 中是否仍有占位性描述（如英文任务占位、临时标记、未定义术语）；全部删净。
- [ ] **Type consistency:** 再核对一次文档与测试里使用的术语是否完全一致：
  - “调查线索（用户提供，未验证）”
  - “现象归类与不确定性”
  - “候选修复方向（人工执行）”
  - `trigger_reboot.on_fail.collectors`

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-20-le-zygote-diagnosis-and-patch-draft.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - 我按 task 派发独立子 agent 执行，并在 task 间做 review
2. **Inline Execution** - 我在当前会话里按 plan 连续执行，实现中途 checkpoint review
