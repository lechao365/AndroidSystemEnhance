# harness 业界对齐（快检/lint/自度量/覆盖率/审批独立）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地已确认的五项 harness 业界对齐修复——P0-A 业务快检进 CI + workspace-verify `--quick`；P0-B ruff/clang 静态检查门禁化；P0-C 自度量统计工具；P1-A 覆盖率采集闭环；P1-B promote 审批独立性（修复 KI-20260907-001）。

**Architecture:** 以既有"检查器脚本 → selfcheck 并行 spawn → `*_rc` 进 REQUIRED_RC_KEYS → ws_report/CI 判红"链为骨架，新增 3 个检查器（check_ruff / check_host_tests / metrics）与 2 个验证脚本（ws_coverage / ws_verify_chain --quick），扩展收据字段（flake_count / coverage），并为 promote 加审批独立校验。全部新增检查器遵循 CDP-DOD-001 三要件：有调用方、破坏即判红用例、rc 进 REQUIRED_RC_KEYS。

**Tech Stack:** Python 3.8+、pytest、ruff、gcc/make（内核 host 单测）、clang-format/clang-tidy（CI 静态检查）、GitHub Actions。

**前置约束（每阶段共用）：**
- 新增/修改 `*_rc` 必须三处同源同步：`selfcheck.REQUIRED_RC_KEYS`、`.github/workflows/selfcheck.yml` 的 `for rc_name in` 循环、`harness/lib/tests/test_workflow_ci.py`（集合断言自动覆盖，只须同步前两处）。
- 新增治理检查器须登记进 `harness/lib/check_hot_path_scan.py::_HOT_PATHS`。
- 所有提交遵循仓内 commit-msg 规范：`<中文type>(<scope>): <描述>`（新增/修复/重构/文档/构建/杂项）。
- 每个任务先写失败测试再实现（TDD）。

---

## Phase 1 — P0-B ruff 静态检查门禁化

### Task 1: ruff 配置与依赖声明

**Files:**
- Create: `ruff.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: 写 ruff 配置**

创建 `ruff.toml`（仓根）：

```toml
# ruff 静态检查配置（P0-B 门禁化）
# select：安全默认集 E4/E7/E9/F（语法/未定义名/未用导入等确定性错误）
# ignore E741：ambiguous-variable-name 为风格噪音（存量 18 处单字符循环变量），低价值放行
# line-length 与 C++ 侧对齐 100
line-length = 100
target-version = "py38"

[lint]
select = ["E4", "E7", "E9", "F"]
ignore = ["E741"]

[lint.per-file-ignores]
# E402：sys.path.insert 后延迟 import 属既有模式，显式放行（checker/tools 层）
"harness/skills/**/*.py" = ["E402"]
"harness/lib/**/*.py" = ["E402"]
"code/**/*.py" = ["E402"]
```

- [ ] **Step 2: 更新 requirements.txt**

读取 `requirements.txt`，追加一行：

```
ruff>=0.6
```

- [ ] **Step 3: 验证配置可解析**

Run: `ruff check harness/lib/selfcheck.py`
Expected: 输出该文件的既有违规清单（不报配置错误）。

- [ ] **Step 4: 提交**

```bash
git add ruff.toml requirements.txt
git commit -m "新增(harness): ruff 静态检查配置与依赖声明（P0-B 门禁化前置）"
```

### Task 2: 清理 harness/ 存量 ruff 违规

**Files:**
- Modify: `harness/**/*.py`（--fix 自动 + 手工）

- [ ] **Step 1: 记录基线违规数**

Run: `ruff check harness/ --statistics | tail -3`
Expected: `Found 56 errors.`（或当前实际数，先记录）。

- [ ] **Step 2: 自动修复可 fix 项**

Run: `ruff check harness/ --fix`
Expected: 自动修复 F401/F541/E401 等 16 项（`--fix` 只做安全修复）。

- [ ] **Step 3: 手工修复剩余项**

Run: `ruff check harness/`
Expected 剩余类别与处置：
- `E702`（约 10 处，一行多语句分号）：拆成独立行，逐处确认语义。
- `F841`（约 7 处，未用变量）：删除或改 `_` 前缀，保留真实语义。
- `E731`（1 处 lambda 赋值）：改 `def`。
- `E402`（若仍报）：确认是 `sys.path.insert` 延迟 import 模式则无需处理（per-file-ignores 已豁免）；非此模式的手工调整。
- `E741`：已 ignore，不出现。

- [ ] **Step 4: 验证清零**

Run: `ruff check harness/`
Expected: `All checks passed!`

- [ ] **Step 5: 运行 harness 自检确认无回归**

Run: `python3 -m pytest harness/lib harness/skills/workspace-verify harness/skills/publish-main-base -q 2>&1 | tail -3`
Expected: 全部 passed（清理不改语义，不应破坏测试）。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "重构(harness): 存量 ruff 违规清零（E702/F841/E731/F401/F541）"
```

### Task 3: 新增 check_ruff 检查器（TDD）

**Files:**
- Test: `harness/lib/tests/test_check_ruff.py`
- Create: `harness/lib/check_ruff.py`
- Modify: `harness/lib/check_hot_path_scan.py::_HOT_PATHS`

- [ ] **Step 1: 写失败测试**

创建 `harness/lib/tests/test_check_ruff.py`：

```python
"""check_ruff 检查器测试：rc 判定 + 结论行 + 工具缺失 fail-closed。"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_ruff as cr  # noqa: E402


class TestCheckRuff(unittest.TestCase):
    def test_scan_compliant_returns_zero(self):
        rc, out = cr._run_ruff("harness/lib/check_hot_path_scan.py")
        # 目标文件应已 ruff 合规（Phase 1 Task 2 清零）
        self.assertEqual(rc, 0)
        self.assertIn("ruff_rc=0", out)

    def test_violation_returns_one(self):
        tmp = Path(__file__).resolve().parent / "_ruff_violation_tmp.py"
        try:
            tmp.write_text("import os\nx = os.getpid()\nprint(x)\n",
                           encoding="utf-8")
            rc, out = cr._run_ruff(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("ruff_rc=1", out)
        finally:
            tmp.unlink(missing_ok=True)

    def test_ruff_missing_fail_closed(self):
        # 工具缺失按失败判红（fail-closed：无法检查不得静默绿）
        with mock.patch.object(cr.subprocess, "run",
                               side_effect=FileNotFoundError):
            rc, out = cr._run_ruff("harness/lib/selfcheck.py")
            self.assertEqual(rc, 1)
            self.assertIn("ruff_rc=1", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_check_ruff.py -q 2>&1 | tail -3`
Expected: FAIL（`ModuleNotFoundError: check_ruff`）。

- [ ] **Step 3: 实现 check_ruff.py**

创建 `harness/lib/check_ruff.py`（模式对齐 check_hot_path_scan.py：独立脚本、`--repo` 可注入、stdout 结论行、rc 语义）：

```python
#!/usr/bin/env python3
# ============================================================
# check_ruff.py — ruff 静态检查门禁守卫（P0-B）
# 设计目的：Python 静态检查左移——AI/人工改动 harness Python 后即时获得
#   ruff 反馈。接入 selfcheck 以 ruff_rc 透出，ws_report/CI 判红。
# 判定对象：harness/ 下全部 Python（配置在仓根 ruff.toml）。
# fail-closed：ruff 二进制缺失/执行异常判红（无法检查不得静默绿）。
# 用法：python3 harness/lib/check_ruff.py [--repo <仓根>] [--scope <路径>]
# 退出码：0 合规 / 1 存在违规或工具不可用 / 2 参数错误
# ============================================================

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(scope: str, repo: Path = _ROOT) -> tuple[int, str]:
    """运行 ruff check <scope>，返回 (rc, 机器行)。

    fail-closed：FileNotFoundError（ruff 未安装）与任何非零退出均判红，
    无法检查不得静默绿；rc=0 时结论行带 ruff_rc=0。
    """
    target = str(repo / scope) if not Path(scope).is_absolute() else scope
    try:
        r = subprocess.run(["ruff", "check", target], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=120)
    except FileNotFoundError:
        return 1, "ruff_rc=1 | error: ruff 未安装（pip install ruff）"
    except subprocess.TimeoutExpired:
        return 1, "ruff_rc=1 | error: ruff 超时（>120s）"
    tail = (r.stdout or "").strip().splitlines()
    last = tail[-1] if tail else "（无输出）"
    return r.returncode, f"ruff_rc={r.returncode} | {last}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ruff 静态检查门禁守卫")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    ap.add_argument("--scope", default="harness", help="扫描范围（相对仓根路径）")
    args = ap.parse_args(argv)
    rc, line = _run_ruff(args.scope, Path(args.repo))
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 登记 _HOT_PATHS**

读取 `harness/lib/check_hot_path_scan.py`，在 `_HOT_PATHS` 列表的
`"harness/lib/selfcheck.py",` 行后追加：

```python
    "harness/lib/check_ruff.py",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_check_ruff.py -q 2>&1 | tail -3`
Expected: `3 passed`。

- [ ] **Step 6: 直跑检查器验证**

Run: `python3 harness/lib/check_ruff.py`
Expected: `ruff_rc=0 | All checks passed!`，退出码 0。

- [ ] **Step 7: 提交**

```bash
git add harness/lib/check_ruff.py harness/lib/tests/test_check_ruff.py harness/lib/check_hot_path_scan.py
git commit -m "新增(harness): ruff 检查器接入自检链（ruff_rc，fail-closed）"
```

### Task 4: ruff_rc 接入 selfcheck / ws_report / CI

**Files:**
- Modify: `harness/lib/selfcheck.py`（REQUIRED_RC_KEYS、_spawn_tools、_collect_tools、main 输出）
- Modify: `harness/lib/tests/test_selfcheck*.py`（如存在 REQUIRED_RC_KEYS 集合断言）
- Modify: `.github/workflows/selfcheck.yml`（rc 解析循环 + pip 安装 ruff）

- [ ] **Step 1: 更新 REQUIRED_RC_KEYS**

在 `harness/lib/selfcheck.py` 找到：

```python
REQUIRED_RC_KEYS = ("pytest_rc", "refs_rc", "config_rc", "contract_rc",
                    "pyenv_rc", "ioctl_rc", "manifest_rc",
                    "discipline_rc", "scan_rc")
```

改为（本 Task 只加 ruff_rc；host_rc/metrics_rc 分别在 Task 6/10 加入，避免中间态自检缺键）：

```python
REQUIRED_RC_KEYS = ("pytest_rc", "refs_rc", "config_rc", "contract_rc",
                    "pyenv_rc", "ioctl_rc", "manifest_rc",
                    "discipline_rc", "scan_rc", "ruff_rc")
```

- [ ] **Step 2: main 中并行 spawn ruff（ioctl/manifest 同款模式）**

在 `selfcheck.py::main` 中 `manifest_proc = _spawn_cmd(...)` 之后追加：

```python
    ruff_proc = _spawn_cmd(
        [sys.executable, str(ROOT / "harness" / "lib" / "check_ruff.py")])
```

并在 pytest 跑完的收口段（ioctl/manifest 收口处）追加：

```python
    ruff_rc, ruff_out, _, ruff_dur = _collect_cmd(ruff_proc, "ruff")
```

> 设计说明：ruff/host 走 main 直接 spawn+收口（与 ioctl/manifest 同款），**不改 `_spawn_tools`/`_collect_tools` 签名**（避免动 `run_parallel_tools` 的 5 元组解包），改动面最小。

- [ ] **Step 3: main 输出追加 ruff_rc 段**

在 `selfcheck.py::main` 中 `scan_rc` 段之后、`durs` 段之前追加：

```python
    # ruff 静态检查（P0-B）：ruff_rc 透出，非零交 ws_report 全 *_rc 判红拒写
    parts.append(f"ruff_rc={ruff_rc}")
    ruff_last = last_stdout_line(ruff_out)
    if ruff_last:
        parts.append(ruff_last)
```

并在 `durs:` 行追加 `ruff={ruff_dur:.1f}`。

- [ ] **Step 5: 更新 CI 解析循环 + 安装 ruff**

读取 `.github/workflows/selfcheck.yml`：
1. `pip install` 行改为 `python3 -m pip install --quiet pyyaml pytest pytest-xdist ruff`；
2. `for rc_name in ...` 循环的键集合追加 `ruff_rc`：

```yaml
          for rc_name in pytest_rc refs_rc config_rc contract_rc pyenv_rc ioctl_rc manifest_rc discipline_rc scan_rc ruff_rc; do
```

- [ ] **Step 6: 更新自检键集合测试**

Run: `grep -rn "REQUIRED_RC_KEYS\|test_ci_parses_required_rcs" harness/lib/tests/`
若存在硬编码 9 键集合的测试，改为引用 `REQUIRED_RC_KEYS` 或追加 `ruff_rc`。`test_workflow_ci.py` 的 `test_ci_parses_required_rcs` 自动比对 CI 键集合与 `REQUIRED_RC_KEYS`，Step 5 已同步即自动通过。

- [ ] **Step 6b: 批量更新 test_ws_report.py 自检 fixture（补 ruff_rc）**

`test_ws_report.py` 有 74 处 `--selfcheck "pytest_rc=0 ... scan_rc=<n>"` fixture 传给真实 `ws_report.main`，其必查键校验随 REQUIRED_RC_KEYS 新增而要求补齐。在 `scan_rc=<n>` 后统一追加 `ruff_rc=0`：

Run: `sed -i -E 's/(scan_rc=[0-9]+)/\1 ruff_rc=0/g' harness/skills/workspace-verify/tests/test_ws_report.py`

Run: `grep -c "scan_rc=0 ruff_rc=0" harness/skills/workspace-verify/tests/test_ws_report.py`
Expected: 74（全部命中）。

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py -q 2>&1 | tail -3`
Expected: 全部 passed（无新增键缺键失败）。

- [ ] **Step 7: 运行自检与测试**

Run: `python3 harness/lib/selfcheck.py 2>&1 | grep -o "ruff_rc=[0-9]*"`
Expected: `ruff_rc=0`

Run: `python3 -m pytest harness/lib/tests/test_workflow_ci.py harness/lib/tests/test_check_ruff.py -q 2>&1 | tail -3`
Expected: 全部 passed。

- [ ] **Step 8: 提交**

```bash
git add harness/lib/selfcheck.py .github/workflows/selfcheck.yml harness/lib/tests/
git commit -m "新增(harness): ruff_rc 接入自检链与 CI 门禁（REQUIRED_RC_KEYS 三处同步）"
```

---

## Phase 2 — P0-A 业务快检：内核 host 单测进自检与 CI

### Task 5: 新增 check_host_tests 检查器（内核 host 单测）

**Files:**
- Test: `harness/lib/tests/test_check_host_tests.py`
- Create: `harness/lib/check_host_tests.py`
- Modify: `harness/lib/check_hot_path_scan.py::_HOT_PATHS`

- [ ] **Step 1: 写失败测试**

创建 `harness/lib/tests/test_check_host_tests.py`：

```python
"""check_host_tests 检查器测试：host 单测 rc 判定 + 目录缺失/工具缺失判红。"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_host_tests as cht  # noqa: E402


class TestCheckHostTests(unittest.TestCase):
    def test_run_one_make_test_success(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 0
            rc, out = cht._run_make_test("LcView")
            self.assertEqual(rc, 0)
            self.assertIn("host_rc=0", out)

    def test_run_one_make_test_fail(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 2
            rc, out = cht._run_make_test("LcView")
            self.assertEqual(rc, 1)
            self.assertIn("host_rc=1", out)

    def test_clean_always_runs_after_test(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.side_effect = [mock.Mock(returncode=0), mock.Mock(returncode=0)]
            cht._run_make_test("LcView")
            # 先 make test 后 make clean（清掉产物防污染 git status）
            self.assertEqual(m.call_count, 2)
            self.assertIn("clean", str(m.call_args_list[1]))

    def test_make_dir_missing_returns_one(self):
        rc, out = cht._run_make_test("__nonexistent_module__")
        self.assertEqual(rc, 1)
        self.assertIn("host_rc=1", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_check_host_tests.py -q 2>&1 | tail -3`
Expected: FAIL（`ModuleNotFoundError: check_host_tests`）。

- [ ] **Step 3: 实现 check_host_tests.py**

创建 `harness/lib/check_host_tests.py`：

```python
#!/usr/bin/env python3
# ============================================================
# check_host_tests.py — 内核 host 单测门禁守卫（P0-A 快检）
# 设计目的：内核纯逻辑 host 单测（LcView ring / LcIod read_logic）是
#   gcc 可编译的业务快检层，此前仅文档纪律（S8）无自动链。接入 selfcheck
#   以 host_rc 透出——AI 改动内核纯逻辑后自检即得编译+单测反馈，无需等
#   完整上板链。
# 判定对象：code/rpi5/kernel/new/vendor/lechao/{LcView,LcIod}/tests 的
#   `make test`（编译+运行，-Wall -Wextra -Werror）。
# 产物清理：make test 后必跑 make clean 删除 host_test 二进制，防污染
#   git status（commit_scope/sync 依赖工作树干净）。
# fail-closed：make 缺失/目录缺失判红（编译失败不可静默绿）。
# 用法：python3 harness/lib/check_host_tests.py [--repo <仓根>]
# 退出码：0 全过 / 1 任一失败或工具不可用 / 2 参数错误
# ============================================================

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 内核 host 单测模块（relative：<kernel_new>/<module>/tests 下 make test）
_HOST_TEST_MODULES = ("LcView", "LcIod")


def _module_dir(repo: Path) -> Path:
    return (repo / "code" / "rpi5" / "kernel" / "new"
            / "vendor" / "lechao")


def _run_make_test(module: str, repo: Path = _ROOT) -> tuple[int, str]:
    """在 <module>/tests 下执行 `make test && make clean`，返回 (rc, 机器行)。"""
    d = _module_dir(repo) / module / "tests"
    if not (d / "Makefile").is_file():
        return 1, f"host_rc=1 | error: {module}/tests 缺失（host 单测守卫断链）"
    try:
        r = subprocess.run(["make", "test"], cwd=d, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=300)
    except FileNotFoundError:
        return 1, f"host_rc=1 | error: make 未安装（{module} host 单测无法执行）"
    except subprocess.TimeoutExpired:
        return 1, f"host_rc=1 | error: make test 超时（>300s，{module}）"
    # 无论成败都清产物（防污染工作树）；clean 失败不覆盖 test rc
    try:
        subprocess.run(["make", "clean"], cwd=d, capture_output=True,
                       timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    tail = (r.stdout or "").strip().splitlines()
    last = tail[-1] if tail else "（无输出）"
    return r.returncode, f"host_rc={r.returncode} | {module}: {last}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="内核 host 单测门禁守卫")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    worst = 0
    for mod in _HOST_TEST_MODULES:
        rc, line = _run_make_test(mod, repo)
        print(line)
        worst = max(worst, rc)
    print("OK: 内核 host 单测全部通过" if worst == 0
          else f"FAIL: 内核 host 单测存在失败（rc={worst}）")
    return worst


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 登记 _HOT_PATHS**

在 `harness/lib/check_hot_path_scan.py::_HOT_PATHS` 追加：

```python
    "harness/lib/check_host_tests.py",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_check_host_tests.py -q 2>&1 | tail -3`
Expected: `4 passed`。

- [ ] **Step 6: 直跑检查器验证**

Run: `python3 harness/lib/check_host_tests.py`
Expected: 两行 `host_rc=0 | <module>: ...` + `OK: 内核 host 单测全部通过`，退出码 0。

- [ ] **Step 7: 提交**

```bash
git add harness/lib/check_host_tests.py harness/lib/tests/test_check_host_tests.py harness/lib/check_hot_path_scan.py
git commit -m "新增(harness): 内核 host 单测检查器接入自检链（host_rc，P0-A 快检）"
```

### Task 6: host_rc 接入 selfcheck / CI

**Files:**
- Modify: `harness/lib/selfcheck.py`（REQUIRED_RC_KEYS、_spawn_tools、_collect_tools、main）
- Modify: `.github/workflows/selfcheck.yml`

- [ ] **Step 1: REQUIRED_RC_KEYS 追加 host_rc**

`selfcheck.py::REQUIRED_RC_KEYS` 改为（ruff_rc 已在 Phase 1 Task 4 加入，此处补 host_rc）：

```python
REQUIRED_RC_KEYS = ("pytest_rc", "refs_rc", "config_rc", "contract_rc",
                    "pyenv_rc", "ioctl_rc", "manifest_rc",
                    "discipline_rc", "scan_rc", "ruff_rc", "host_rc")
```

- [ ] **Step 2: _spawn_tools 增加 host**

在 `_spawn_tools` 返回 dict 追加：

```python
        "host": _spawn_cmd(
            [sys.executable, str(ROOT / "harness" / "lib"
                                 / "check_host_tests.py")]),
```

- [ ] **Step 3: _collect_tools 收口 host**

在 `_collect_tools` 中追加（与 ruff 同款）：

```python
    host_rc, host_out, _, host_dur = _collect_cmd(procs["host"], "host")
    tools["host"] = (host_rc, host_out, "")
```

- [ ] **Step 4: main 输出追加 host_rc**

在 ruff_rc 段之后追加：

```python
    # 内核 host 单测（P0-A 快检）：host_rc 透出，非零交 ws_report 判红
    parts.append(f"host_rc={host_rc}")
    host_last = last_stdout_line(host_out)
    if host_last:
        parts.append(host_last)
```

`durs:` 行追加 `host={host_dur:.1f}`。

- [ ] **Step 5: 更新 CI 解析循环**

`.github/workflows/selfcheck.yml` 的 `for rc_name in` 追加 `host_rc`：

```yaml
          for rc_name in pytest_rc refs_rc config_rc contract_rc pyenv_rc ioctl_rc manifest_rc discipline_rc scan_rc ruff_rc host_rc; do
```

- [ ] **Step 6: 运行自检验证**

Run: `python3 harness/lib/selfcheck.py 2>&1 | grep -o "host_rc=[0-9]*"`
Expected: `host_rc=0`

Run: `python3 -m pytest harness/lib/tests/test_workflow_ci.py -q 2>&1 | tail -3`
Expected: passed（CI 键集合与 REQUIRED_RC_KEYS 自动同步）。

- [ ] **Step 6b: 批量更新 test_ws_report.py 自检 fixture（补 host_rc）**

Run: `sed -i -E 's/(scan_rc=[0-9]+ ruff_rc=0)/\1 host_rc=0/g' harness/skills/workspace-verify/tests/test_ws_report.py`

Run: `grep -c "ruff_rc=0 host_rc=0" harness/skills/workspace-verify/tests/test_ws_report.py`
Expected: 74。

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py -q 2>&1 | tail -3`
Expected: 全部 passed。

- [ ] **Step 7: 提交**

```bash
git add harness/lib/selfcheck.py .github/workflows/selfcheck.yml
git commit -m "新增(harness): host_rc 接入自检链与 CI 门禁（内核 host 单测快检）"
```

### Task 7: CI 新增 host-tests 作业（内核 host 单测 + diff 驱动 C/C++ 静态检查）

**Files:**
- Create: `.clang-format`
- Create: `.clang-tidy`
- Modify: `.github/workflows/selfcheck.yml`
- Modify: `harness/lib/tests/test_workflow_ci.py`

> **设计修订（执行期确认）**：原方案对 4 个内核纯逻辑文件做 clang-format 归一属 `code/`
> 业务源码改动，会触发 git-works-push 的 RECEIPT_MISSING 门禁（无收据拒推）。
> 改为 **diff 驱动**：CI 只对相对 `origin/main` 改动的 C/H 文件做 clang-format
> 校验与（纯逻辑文件的）clang-tidy 分析；本批无 C 改动则跳过通过。零 `code/`
> 改动、不触发推送门禁，同时仍是未来 C 改动的真实门禁。

- [ ] **Step 1: 写 .clang-format**

创建仓根 `.clang-format`：

```yaml
# C/C++ 风格（P0-B）：LLVM 基 + Allman 大括号 + 4 空格缩进 + 100 列。
# 经 diff 驱动检查：仅对相对 origin/main 改动的 C/H 文件强制校验，
# 存量文件不动（避免 code/ 业务源码改动触发 git-works-push 收据门禁）。
BasedOnStyle: LLVM
IndentWidth: 4
ContinuationIndentWidth: 4
ColumnLimit: 100
SortIncludes: false
BreakBeforeBraces: Allman
```

- [ ] **Step 2: 写 .clang-tidy**

创建仓根 `.clang-tidy`：

```yaml
# clang-tidy 静态分析（P0-B）：仅对 host 单测纯逻辑文件（独立可编译，
# 带 -I 编译旗标）执行；clang-analyzer/bugprone 系列，analyzer 警告升错误。
Checks: 'clang-analyzer-*,bugprone-*'
WarningsAsErrors: 'clang-analyzer-*'
HeaderFilterRegex: '.*'
```

- [ ] **Step 3: 写失败测试（CI 作业结构）**

在 `harness/lib/tests/test_workflow_ci.py` 追加：

```python
    def test_host_tests_job_runs_make_test(self):
        # P0-A：host-tests 作业须跑两个内核 host 单测 make test（业务快检）
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        for d in ("LcView", "LcIod"):
            self.assertIn(f"make -C code/rpi5/kernel/new/vendor/lechao/{d}/tests test", run_steps)

    def test_host_tests_job_static_check_diff_driven(self):
        # P0-B：C/C++ 静态检查须 diff 驱动（相对 origin/main，无改动跳过）——
        # 存量 code/ 文件不强制归一（避免 RECEIPT_MISSING 推送门禁）
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn("git fetch --quiet origin main", run_steps)
        self.assertIn("git diff --name-only", run_steps)
        self.assertIn("clang-format --dry-run --Werror", run_steps)
        self.assertIn("clang-tidy", run_steps)

    def test_host_tests_job_installs_clang_tools(self):
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn("apt-get", run_steps)
```

- [ ] **Step 4: 运行测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_workflow_ci.py -q 2>&1 | tail -5`
Expected: 新增 3 用例 FAIL（`KeyError: 'host-tests'`）。

- [ ] **Step 5: 修改 selfcheck.yml 新增 host-tests 作业**

在 `jobs:` 下追加（保持最小权限、无 secrets、action SHA 固定；apt 安装 clang 工具；`git fetch origin main` 提供 diff 基准；diff 驱动静态检查）：

```yaml
  host-tests:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

      - name: 安装 clang 静态检查工具
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y -qq clang-format clang-tidy

      - name: 内核 host 单测（LcView / LcIod）
        run: |
          make -C code/rpi5/kernel/new/vendor/lechao/LcView/tests test
          make -C code/rpi5/kernel/new/vendor/lechao/LcIod/tests test

      - name: C/C++ 静态检查（diff 驱动，无 C 改动跳过）
        run: |
          set -euo pipefail
          git fetch --quiet origin main || true
          CHANGED=$(git diff --name-only --diff-filter=ACM origin/main...HEAD -- '*.c' '*.h' || true)
          if [ -z "$CHANGED" ]; then
            echo "本批无 C/C++ 改动，静态检查跳过"
            exit 0
          fi
          echo "检查文件: $CHANGED"
          for f in $CHANGED; do
            clang-format --dry-run --Werror "$f"
          done
          # clang-tidy 仅对 host 单测纯逻辑文件（独立可编译，带 -I 旗标）
          for f in $CHANGED; do
            case "$f" in
              code/rpi5/kernel/new/vendor/lechao/LcView/lcview_ring_logic.c)
                clang-tidy -p . "$f" -- -Icode/rpi5/kernel/new/vendor/lechao/LcView ;;
              code/rpi5/kernel/new/vendor/lechao/LcIod/lciod_read_logic.c)
                clang-tidy -p . "$f" -- -Icode/rpi5/kernel/new/vendor/lechao/LcIod ;;
            esac
          done
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_workflow_ci.py -q 2>&1 | tail -3`
Expected: 全部 passed（含新增 3 用例）。

- [ ] **Step 7: 本地验证内核 host 单测仍绿**

Run: `python3 harness/lib/check_host_tests.py`
Expected: `OK: 内核 host 单测全部通过`。

- [ ] **Step 8: 提交**

```bash
git add .clang-format .clang-tidy .github/workflows/selfcheck.yml harness/lib/tests/test_workflow_ci.py
git commit -m "新增(harness): CI host-tests 作业（内核 host 单测 + diff 驱动 C/C++ 静态检查）"
```

### Task 8: workspace-verify 新增 `--quick` 快检模式

**Files:**
- Modify: `harness/skills/workspace-verify/ws_verify_chain.py`
- Modify: `harness/skills/workspace-verify/tests/test_ws_verify_chain.py`

- [ ] **Step 1: 写失败测试**

在 `harness/skills/workspace-verify/tests/test_ws_verify_chain.py` 追加：

```python
class TestQuickMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        envpatcher = mock.patch.dict("os.environ", {}, clear=False)
        envpatcher.start()
        self.addCleanup(envpatcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_quick_runs_sync_host_and_selfcheck_no_receipt(self):
        # --quick：只跑 sync + host-tests + selfcheck，不触碰设备、不落收据
        ctor, proc = _fake_popen(0)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR",
                                  Path(self._tmp.name) / "runs") as runs, \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK), \
                mock.patch.object(wc, "_build_argv",
                                  side_effect=wc._build_argv):
            rc, result = wc.run_quick(use_locks=False)
        names = _script_names(ctor.call_args_list)
        self.assertEqual(names, ["sync_code_to_workspace.py",
                                 "check_host_tests.py"])
        self.assertEqual(rc, 0)
        # 不落运行态/收据
        self.assertFalse(list(runs.glob("*.json")) if runs.exists() else False)

    def test_quick_host_fail_returns_1(self):
        def _popen(argv, **kw):
            proc = mock.Mock()
            proc.wait = mock.Mock(return_value=1
                                  if "check_host_tests" in str(argv) else 0)
            return proc
        with mock.patch.object(wc.subprocess, "Popen", _popen), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, _ = wc.run_quick(use_locks=False)
        self.assertEqual(rc, 1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py::TestQuickMode -q 2>&1 | tail -3`
Expected: FAIL（`AttributeError: module 'ws_verify_chain' has no attribute 'run_quick'`）。

- [ ] **Step 3: 实现 run_quick 与 --quick CLI**

在 `ws_verify_chain.py` 中 `run_chain` 定义之后、`_chain_mark` 之前插入：

```python
# ── P0-A 快检模式（--quick）────────────
# 语义：AI 编辑内核纯逻辑/单测后的廉价快检——只做 code→workspace 同步 +
#   内核 host 单测 + 自检，跳过 connect/push/acceptance/report（不触碰
#   设备、不落收据）。用于在走完整上板链前快速确认「能编译、纯逻辑单测
#   过、harness 健康」，反馈分钟级、不占真机。
_QUICK_STEPS = (
    "sync",      # sync_code_to_workspace.py --auto
    "host",      # check_host_tests.py（内核 host 单测）
)
_QUICK_TIMEOUTS = {"sync": 900, "host": 300}


def run_quick(use_locks=True):
    """快检模式：sync + host 单测 + selfcheck，不落收据。

    返回 (rc, result_dict)。rc=0 全过 / 1 任一步失败 / 3 锁占用。
    result 含 steps（sync/host 的 rc 与耗时）与 selfcheck 摘要（诊断）。
    锁模式与 run_chain 同款（ws_lock 模块级已 import；nullcontext 顶部已 import）。
    """
    steps, overall = [], "pass"
    started_at = time.time()
    try:
        with (ws_lock.verify_locks() if use_locks else nullcontext()):
            for name in _QUICK_STEPS:
                t0 = time.time()
                if name == "sync":
                    argv = [sys.executable, str(_SYNC), "--auto"]
                else:
                    argv = [sys.executable,
                            str(_SCRIPT_DIR.parents[1] / "lib"
                                / "check_host_tests.py")]
                rc, canceled = _run_step(argv, _QUICK_TIMEOUTS[name])
                steps.append({"name": name, "rc": rc, "start": t0,
                              "end": time.time(), "canceled": canceled})
                if canceled or rc is None or rc != 0:
                    overall = "fail"
                    break
    except ws_lock.LockHeld as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3, {"overall": "fail", "exit_rc": 3, "steps": [],
                   "error": str(exc)}
    # 自检摘要只作诊断（快检不落收据，不判红）
    selfcheck_text = _run_selfcheck()
    result = {"mode": "quick", "overall": overall,
              "exit_rc": 0 if overall == "pass" else 1,
              "steps": steps, "started_at": started_at,
              "ended_at": time.time(),
              "selfcheck": selfcheck_text.splitlines()[-1][:200]
              if selfcheck_text else ""}
    return result["exit_rc"], result
```

在 `main` 的 argparse 增加：

```python
    ap.add_argument("--quick", action="store_true",
                    help="快检模式：sync + 内核 host 单测 + 自检，不落收据"
                         "（AI 编辑纯逻辑后的廉价反馈，不占真机）")
```

并在 `main` 中 `args = ap.parse_args(argv)` 之后插入：

```python
    if args.quick:
        rc, result = run_quick()
        print(json.dumps(result, ensure_ascii=False))
        return rc
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q 2>&1 | tail -3`
Expected: 全部 passed（含新增 TestQuickMode 2 用例）。

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/ws_verify_chain.py harness/skills/workspace-verify/tests/test_ws_verify_chain.py
git commit -m "新增(harness): workspace-verify --quick 快检模式（sync+host 单测+自检，不落收据）"
```

---

## Phase 3 — P0-C harness 自度量统计工具

### Task 9: 新增 metrics.py 统计工具

**Files:**
- Test: `harness/lib/tests/test_metrics.py`
- Create: `harness/lib/metrics.py`

- [ ] **Step 1: 写失败测试**

创建 `harness/lib/tests/test_metrics.py`：

```python
"""metrics.py 自度量工具测试：聚合收据/趋势/known-issues，缺数据容错。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "skills"
                         / "cross-device" / "lib" / "python"))

import metrics as mt  # noqa: E402


def _receipt(verify_dir, batch_id, result, elapsed):
    from cdp_receipt import Receipt, write_receipt
    r = Receipt(batch_id=batch_id, result=result, elapsed_s=elapsed,
                verify_mode="board")
    write_receipt(r, "body")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.verify = self.root / "verify-results"
        self.verify.mkdir()
        self.issues = self.root / "known-issues"
        self.issues.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_empty_dir_returns_zero_stats(self):
        stats = mt.compute([], [], [])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["pass_rate"], 0.0)

    def test_counts_and_rates(self):
        for i, res in enumerate(["pass", "pass", "fail", "skip"]):
            _receipt(self.verify, f"b{i}", res, 30 + i)
        files = sorted(self.verify.glob("*.md"))
        stats = mt.compute(mt.load_receipts(self.verify),
                           mt.load_trend(self.verify), [])
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["pass"], 2)
        self.assertEqual(stats["fail"], 1)
        self.assertEqual(stats["skip"], 1)
        self.assertAlmostEqual(stats["pass_rate"], 0.5)

    def test_elapsed_stats(self):
        for i, res in enumerate(["pass", "pass"]):
            _receipt(self.verify, f"b{i}", res, 40 + i * 40)
        stats = mt.compute(mt.load_receipts(self.verify), [], [])
        self.assertEqual(stats["avg_elapsed_s"], 60)
        self.assertEqual(stats["p90_elapsed_s"], 80)

    def test_known_issues_board(self):
        (self.issues / "KI-1.md").write_text(
            "- kind: flake\n- status: open\n", encoding="utf-8")
        (self.issues / "KI-2.md").write_text(
            "- kind: idle-eligible\n- status: fixed\n", encoding="utf-8")
        board = mt.ki_board(self.issues)
        self.assertEqual(board["flake"]["open"], 1)
        self.assertEqual(board["idle-eligible"]["fixed"], 1)

    def test_render_and_json(self):
        stats = mt.compute([], [], [])
        self.assertIsInstance(mt.render_stats(stats), str)
        parsed = json.loads(mt.render_stats(stats, as_json=True))
        self.assertIn("total", parsed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_metrics.py -q 2>&1 | tail -3`
Expected: FAIL（`ModuleNotFoundError: metrics`）。

- [ ] **Step 3: 实现 metrics.py**

创建 `harness/lib/metrics.py`：

```python
#!/usr/bin/env python3
# ============================================================
# metrics.py — harness 自度量统计工具（P0-C）
# 设计目的：AI 一条命令自查项目健康度——聚合 verify 收据 / 趋势行 /
#   known-issues，输出 pass 率、flake 率、验证时长分布、KI 状态板。
#   只读聚合（不写仓内文件）；对空目录/坏数据容错（缺字段不崩）。
# 接入：selfcheck 以 metrics_rc 透出（跑通即 0，聚合异常判红）。
# 用法：python3 harness/lib/metrics.py --report [--json]
#   [--verify-dir <dir>] [--issues-dir <dir>]
# 退出码：0 聚合成功（无论数据多少）/ 1 聚合异常 / 2 参数错误
# ============================================================

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VERIFY_DIR = _ROOT / "data" / "verify-results"
_ISSUES_DIR = _ROOT / "data" / "known-issues"

# 趋势行 result 列（第 3 字段）
_TREND_RESULT_RE = re.compile(r"^\S+\s+\S+\s+(\S+)\s+(\S+)\s+(.*)$")
# known-issues 头字段
_KI_FIELD_RE = re.compile(r"^- (\w+): (.*)$", re.MULTILINE)


def load_receipts(verify_dir: Path) -> list[dict]:
    """读 verify-results/*.md，返回字段 dict 列表（排除 trend.md；解析容错）。"""
    out = []
    if not verify_dir.is_dir():
        return out
    for f in sorted(verify_dir.glob("*.md")):
        if f.name == "trend.md":
            continue
        try:
            txt = f.read_text(encoding="utf-8")
            header = txt.split("\n## body", 1)[0]
            r = {"_file": f.name}
            for m in _KI_FIELD_RE.finditer(header):
                r[m.group(1)] = m.group(2)
            out.append(r)
        except (OSError, UnicodeDecodeError):
            continue
    return out


def load_trend(verify_dir: Path) -> list[dict]:
    """解析 trend.md 每行 {result, stage, summary}；行格式非法跳过。"""
    out = []
    trend = verify_dir / "trend.md"
    if not trend.is_file():
        return out
    for ln in trend.read_text(encoding="utf-8").splitlines():
        m = _TREND_RESULT_RE.match(ln)
        if m:
            out.append({"result": m.group(1), "stage": m.group(2),
                        "summary": m.group(3)})
    return out


def load_known_issues(issues_dir: Path) -> list[dict]:
    """读 known-issues/*.md 头字段（排除 index.md）；解析容错。"""
    out = []
    if not issues_dir.is_dir():
        return out
    for f in sorted(issues_dir.glob("*.md")):
        if f.name == "index.md":
            continue
        try:
            txt = f.read_text(encoding="utf-8")
            r = {"_file": f.name}
            for m in _KI_FIELD_RE.finditer(txt):
                r[m.group(1)] = m.group(2)
            out.append(r)
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def compute(receipts, trend_lines, issues) -> dict:
    """聚合统计（缺数据容错，任何输入长度均可）。"""
    total = len(receipts)
    counts = {"pass": 0, "fail": 0, "skip": 0, "revert": 0, "other": 0}
    elapsed = []
    for r in receipts:
        res = (r.get("result") or "other").lower()
        counts[res if res in counts else "other"] += 1
        if r.get("elapsed_s") not in ("", None):
            elapsed.append(_num(r.get("elapsed_s")))
    total_pass = counts["pass"]
    pass_rate = total_pass / total if total else 0.0
    flake_count = sum(1 for i in issues if i.get("kind") == "flake")
    return {
        "total": total,
        "pass": counts["pass"], "fail": counts["fail"],
        "skip": counts["skip"], "revert": counts["revert"],
        "pass_rate": round(pass_rate, 3),
        "avg_elapsed_s": round(sum(elapsed) / len(elapsed), 1)
        if elapsed else 0,
        "p90_elapsed_s": sorted(elapsed)[int(len(elapsed) * 0.9) - 1]
        if elapsed else 0,
        "trend_total": len(trend_lines),
        "flake_count": flake_count,
        "known_issues_total": len(issues),
    }


def ki_board(issues_dir: Path) -> dict:
    """known-issues 状态板：{kind: {status: count}}。"""
    board: dict[str, dict[str, int]] = {}
    for i in load_known_issues(issues_dir):
        kind = i.get("kind") or "unknown"
        status = i.get("status") or "unknown"
        board.setdefault(kind, {})
        board[kind][status] = board[kind].get(status, 0) + 1
    return board


def render_stats(stats: dict, as_json: bool = False) -> str:
    """渲染统计（文本或 JSON）。"""
    if as_json:
        return json.dumps(stats, ensure_ascii=False, sort_keys=True,
                          indent=2)
    return "\n".join([
        f"verify 收据总数: {stats['total']}",
        f"  pass={stats['pass']} fail={stats['fail']} "
        f"skip={stats['skip']} revert={stats['revert']}",
        f"pass 率: {stats['pass_rate']:.1%}",
        f"平均验证时长: {stats['avg_elapsed_s']}s  "
        f"P90: {stats['p90_elapsed_s']}s",
        f"trend 行数: {stats['trend_total']}",
        f"flake known-issues: {stats['flake_count']}  "
        f"known-issues 总数: {stats['known_issues_total']}",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="harness 自度量统计")
    ap.add_argument("--report", action="store_true",
                    help="输出统计报表（缺省模式）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出")
    ap.add_argument("--verify-dir", default=str(_VERIFY_DIR))
    ap.add_argument("--issues-dir", default=str(_ISSUES_DIR))
    args = ap.parse_args(argv)
    try:
        receipts = load_receipts(Path(args.verify_dir))
        trend = load_trend(Path(args.verify_dir))
        issues = load_known_issues(Path(args.issues_dir))
        stats = compute(receipts, trend, issues)
        if args.json:
            out = render_stats(stats, as_json=True)
        else:
            board = ki_board(Path(args.issues_dir))
            header = "metrics_rc=0"
            body = render_stats(stats)
            board_lines = " | ".join(
                f"{k}:{v}" for k, v in
                ({"open": board.get("flake", {}).get("open", 0),
                  "fixed": board.get("flake", {}).get("fixed", 0)}).items())
            out = f"{header} | flake: {board_lines}\n{body}"
        print(out)
        return 0
    except Exception as e:  # 聚合异常判红（自检 metrics_rc 依赖）
        print(f"metrics_rc=1 | error: 聚合异常: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_metrics.py -q 2>&1 | tail -3`
Expected: `5 passed`。

- [ ] **Step 5: 直跑工具验证**

Run: `python3 harness/lib/metrics.py --report`
Expected: `metrics_rc=0` 开头 + 统计文本（对真实 data/ 输出）。

Run: `python3 harness/lib/metrics.py --report --json | python3 -m json.tool >/dev/null && echo VALID`
Expected: `VALID`。

- [ ] **Step 6: 提交**

```bash
git add harness/lib/metrics.py harness/lib/tests/test_metrics.py
git commit -m "新增(harness): 自度量统计工具 metrics.py（pass率/flake率/时长分布/KI板）"
```

### Task 10: metrics_rc 接入 selfcheck / CI + 收据 flake_count 字段

**Files:**
- Modify: `harness/lib/selfcheck.py`（REQUIRED_RC_KEYS、main 同步调用）
- Modify: `.github/workflows/selfcheck.yml`
- Modify: `harness/skills/cross-device/lib/python/cdp_receipt.py`（_FIELDS 加 flake_count）
- Modify: `harness/skills/workspace-verify/ws_report.py`（解析 selfcheck 计数 flake_count）
- Modify: `harness/skills/workspace-verify/tests/test_ws_report.py`

- [ ] **Step 1: REQUIRED_RC_KEYS 追加 metrics_rc**

`selfcheck.py::REQUIRED_RC_KEYS` 改为：

```python
REQUIRED_RC_KEYS = ("pytest_rc", "refs_rc", "config_rc", "contract_rc",
                    "pyenv_rc", "ioctl_rc", "manifest_rc",
                    "discipline_rc", "scan_rc", "ruff_rc", "host_rc",
                    "metrics_rc")
```

- [ ] **Step 2: selfcheck main 同步调用 metrics**

在 `selfcheck.py::main` 中 pyenv 段附近（同步执行，非 spawn）追加：

```python
    # 自度量统计（P0-C）：metrics.py --report 跑通即 0（聚合异常判红）。
    # metrics.py 首行自带 metrics_rc=0（机器行在前，报表体在后），last 行
    # 是报表末行——以正则定位 metrics_rc= 机器行，不依赖末行位置。
    _metrics_t0 = time.time()
    met_rc, met_out, _, met_dur = timed_run(
        [sys.executable, str(ROOT / "harness" / "lib" / "metrics.py"),
         "--report"], timeout=120)
    parts.append(f"metrics_rc={met_rc}")
    m = re.search(r"metrics_rc=(\d+)", met_out)
    if m and int(m.group(1)) == 0:
        parts.append("OK: 自度量聚合成功")
    elif met_rc != 0:
        parts.append("error: 自度量聚合失败")
```

`durs:` 行追加 `metrics={met_dur:.1f}`。

- [ ] **Step 3: 更新 CI 解析循环**

`.github/workflows/selfcheck.yml` 的 `for rc_name in` 追加 `metrics_rc`。

- [ ] **Step 4: 收据字段加 flake_count**

`cdp_receipt.py::_FIELDS` 在 `("host_env", ""),` 前追加：

```python
    # P0-C：本批 selfcheck 登记的 flake 数（ws_report 从 selfcheck 文本解析；
    # 旧收据无此行 → 默认空，metrics 聚合容错）
    ("flake_count", ""),
```

- [ ] **Step 5: ws_report 解析 flake_count**

在 `ws_report.py::main` 中自检段处理之后（`args.selfcheck = " | ".join(...)` 之后）追加：

```python
    # P0-C：flake 计数（本批 selfcheck 登记的 KIR-002 抖动条目数），供
    # metrics 聚合 flake 率；缺省 0
    if args.selfcheck.strip():
        args.flake_count = str(len(re.findall(r"\bflake:\s+\S+",
                                              args.selfcheck)))
    else:
        args.flake_count = "0"
```

并在 argparse 增加：

```python
    ap.add_argument("--flake-count", default="",
                    help="本批 selfcheck 登记的 flake 数（缺省从 --selfcheck "
                         "文本解析；供 metrics 聚合 flake 率）")
```

在 Receipt 构造处传 `flake_count=args.flake_count`。

- [ ] **Step 6: 写 ws_report flake 计数测试**

在 `harness/skills/workspace-verify/tests/test_ws_report.py` 的 `TestWsReport` 类内追加（复用既有 `_write`/`_dir` helper，模式对齐 `test_mode_a_normal`）：

```python
    _RC = ("pytest_rc=0 refs_rc=0 config_rc=0 contract_rc=0 pyenv_rc=0 "
           "ioctl_rc=0 manifest_rc=0 discipline_rc=0 scan_rc=0 "
           "ruff_rc=0 host_rc=0 metrics_rc=0")

    def test_flake_count_parsed_from_selfcheck(self):
        # P0-C：selfcheck 文本含 "flake:" 行 → 收据 flake_count 计数；
        # 无 flake 行 → 0
        from cdp_receipt import Receipt  # noqa: E402
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        selfcheck_flake = (self._RC + " | 120 passed, 2 skipped in 5.0s"
                           " | flake: harness/lib/tests/test_x.py "
                           "round=1 first=b1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "flake 计数",
                                 "--selfcheck", selfcheck_flake])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        r, errs = Receipt.from_text(details[0].read_text(encoding="utf-8"))
        self.assertFalse(errs)
        self.assertEqual(r.flake_count, "1")

    def test_flake_count_zero_when_no_flake_line(self):
        from cdp_receipt import Receipt  # noqa: E402
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "无 flake",
                                 "--selfcheck",
                                 self._RC + " | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        r, _ = Receipt.from_text(details[0].read_text(encoding="utf-8"))
        self.assertEqual(r.flake_count, "0")
```

> 注：`_RC` 常量须与 Phase 1~3 全部 rc 键一致（ruff_rc/host_rc/metrics_rc 已含）；新用例的 `--selfcheck` 若漏键，会被 ws_report 必查键校验拒写——这正是门禁的回归防护。

- [ ] **Step 7: 运行全部受影响测试**

Run: `python3 -m pytest harness/lib/tests/test_metrics.py harness/skills/workspace-verify/tests/test_ws_report.py harness/lib/tests/test_workflow_ci.py -q 2>&1 | tail -3`
Expected: 全部 passed。

- [ ] **Step 7b: 批量更新 test_ws_report.py 自检 fixture（补 metrics_rc）**

Run: `sed -i -E 's/(scan_rc=[0-9]+ ruff_rc=0 host_rc=0)/\1 metrics_rc=0/g' harness/skills/workspace-verify/tests/test_ws_report.py`

Run: `grep -c "host_rc=0 metrics_rc=0" harness/skills/workspace-verify/tests/test_ws_report.py`
Expected: 74。

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py -q 2>&1 | tail -3`
Expected: 全部 passed。

- [ ] **Step 8: 自检验证**

Run: `python3 harness/lib/selfcheck.py 2>&1 | grep -o "metrics_rc=[0-9]*"`
Expected: `metrics_rc=0`

- [ ] **Step 9: 提交**

```bash
git add harness/lib/selfcheck.py .github/workflows/selfcheck.yml harness/skills/cross-device/lib/python/cdp_receipt.py harness/skills/workspace-verify/ws_report.py harness/skills/workspace-verify/tests/test_ws_report.py
git commit -m "新增(harness): metrics_rc 接入自检链 + 收据 flake_count 字段（自度量闭环）"
```

---

## Phase 4 — P1-A 覆盖率采集闭环

### Task 11: 新增 ws_coverage.py 覆盖率采集

**Files:**
- Test: `harness/skills/workspace-verify/tests/test_ws_coverage.py`
- Create: `harness/skills/workspace-verify/ws_coverage.py`

- [ ] **Step 1: 写失败测试**

创建 `harness/skills/workspace-verify/tests/test_ws_coverage.py`：

```python
"""ws_coverage.py 覆盖率采集测试：产物判定 + 降级语义 + 自描述 JSON。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_coverage as wc  # noqa: E402


class TestWsCoverage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        (self.out / "target" / "product" / "rpi5" / "data" / "nativetest64"
         / "lechao_lcview_unit_test").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def test_no_coverage_artifacts_degrades(self):
        # 无 gcda/gcno/profraw 产物 → 如实标注 unavailable（不门禁不假绿）
        data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "unavailable")

    def test_gcda_present_sets_targets(self):
        gcda = (self.out / "target" / "product" / "rpi5" / "data"
                / "nativetest64" / "lechao_lcview_unit_test" / "c.gcda")
        gcda.write_bytes(b"gcda")
        with mock.patch.object(wc, "_run_lcov") as m:
            m.return_value = (0, "lines: 50.0% of 100")
            data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "ok")
        self.assertIn("lechao_lcview_unit_test", data["targets"])

    def test_lcov_failure_degrades(self):
        gcda = (self.out / "target" / "product" / "rpi5" / "data"
                / "nativetest64" / "lechao_lcview_unit_test" / "c.gcda")
        gcda.write_bytes(b"gcda")
        with mock.patch.object(wc, "_run_lcov") as m:
            m.return_value = (1, "lcov error")
            data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "partial")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_coverage.py -q 2>&1 | tail -3`
Expected: FAIL（`ModuleNotFoundError: ws_coverage`）。

- [ ] **Step 3: 实现 ws_coverage.py**

创建 `harness/skills/workspace-verify/ws_coverage.py`：

```python
#!/usr/bin/env python3
# ============================================================
# ws_coverage.py — 覆盖率采集（P1-A，一期只记录不门禁）
# 设计目的：把 native_coverage: true 插桩从"摆设"变为收据证据——测试真跑
#   后采集 llvm 覆盖数据，产出覆盖率 JSON 随收据入库跨批可 diff。
# 一期语义（对齐 lcview-perf"只报数不设门禁"先例）：status 三态——
#   ok（有覆盖数据并算出）/ partial（有产物但 lcov 失败）/ unavailable
#   （无覆盖产物，如实标注不假绿）。不做阈值门禁。
# 降级路径：设备侧 .gcda 回传不可行时（无 adb/无 llvm-cov）→ 编译期静态
#   覆盖不可得 → status=unavailable 并注明原因，不阻断主流程。
# 用法：python3 ws_coverage.py [--product rpi5] [--out <aosp out>]
#   [--result-file <json>]
# 退出码：0 采集完成（含降级）/ 1 采集异常（不应发生） / 2 参数错误
# ============================================================

import argparse
import subprocess
import sys
import uuid
from pathlib import Path


def _run_lcov(args):
    """运行 lcov（llvm-cov 前端），返回 (rc, stdout)。缺失返 (1, "")。"""
    try:
        r = subprocess.run(["lcov"] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=300)
        return r.returncode, r.stdout
    except FileNotFoundError:
        return 1, ""
    except subprocess.TimeoutExpired:
        return 1, ""


def _iter_gcda(products: list[Path]):
    """在 products 下找 .gcda（设备侧回传产物）与 .profraw（编译期插桩）。"""
    for base in products:
        if not base.is_dir():
            continue
        for p in base.rglob("*.gcda"):
            yield p
        for p in base.rglob("*.profraw"):
            yield p


def collect(out: Path, product: str = "rpi5") -> dict:
    """采集覆盖率，返回自描述 dict（status/targets/reason）。

    targets: {name: lines_pct}——gcda 所在 nativetest 目录名即 target 名。
    """
    product_dir = out / "target" / "product" / product
    gcda_files = list(_iter_gcda([product_dir]))
    if not gcda_files:
        return {"status": "unavailable",
                "reason": "无 .gcda/.profraw 覆盖产物（未启用 native_coverage "
                          "插桩或设备侧未回传），如实标注不门禁",
                "targets": {}}
    targets = {}
    status = "ok"
    for p in gcda_files:
        # gcda 所在 <nativetest>/<name>/ 目录名 = target 名
        name = p.parent.name if p.parent.parent.name in (
            "nativetest", "nativetest64", "testcases") else p.parent.name
        rc, out_text = _run_lcov([
            "--capture", "--directory", str(p.parent),
            "--output-file", "/dev/null", "--summary"])
        if rc == 0:
            import re
            m = re.search(r"lines?\.\.\.\.\.\.\s*([\d.]+)%", out_text)
            targets[name] = float(m.group(1)) if m else None
        else:
            status = "partial"
            targets[name] = None
    return {"status": status, "targets": targets, "reason": ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="覆盖率采集（P1-A 只记录不门禁）")
    ap.add_argument("--product", default="rpi5")
    ap.add_argument("--out", default="", help="AOSP out 目录")
    ap.add_argument("--result-file", default="",
                   help="自描述覆盖率产物 JSON 路径（原子写）")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else Path("").resolve()
    data = collect(out, args.product)
    data["run_id"] = __import__("os").environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    if args.result_file:
        from verify_common import atomic_write_json  # noqa: E402
        atomic_write_json(Path(args.result_file), data)
    import json
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_coverage.py -q 2>&1 | tail -3`
Expected: `3 passed`。

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/ws_coverage.py harness/skills/workspace-verify/tests/test_ws_coverage.py
git commit -m "新增(harness): ws_coverage 覆盖率采集（ok/partial/unavailable 三态，只记录不门禁）"
```

### Task 12: 收据 coverage 字段 + ws_verify_chain 可选 coverage 步

**Files:**
- Modify: `harness/skills/cross-device/lib/python/cdp_receipt.py`（_FIELDS 加 coverage）
- Modify: `harness/skills/workspace-verify/ws_report.py`（--coverage-file 参数）
- Modify: `harness/skills/workspace-verify/ws_verify_chain.py`（--coverage 可选步）
- Modify: `harness/skills/workspace-verify/tests/test_ws_report.py` / `test_ws_verify_chain.py`

- [ ] **Step 1: 收据字段加 coverage**

`cdp_receipt.py::_FIELDS` 在 `("flake_count", ""),` 后追加：

```python
    # P1-A：覆盖率采集证据（ws_coverage 自描述 JSON 单行；只记录不门禁，
    # status 三态 ok/partial/unavailable）。旧收据无此行 → 默认空，兼容。
    ("coverage", ""),
```

- [ ] **Step 2: ws_report 加 --coverage-file**

在 `ws_report.py::main` argparse 增加：

```python
    ap.add_argument("--coverage-file", default="",
                    help="ws_coverage 覆盖率产物 JSON 路径（写入收据 coverage "
                         "字段；只记录不门禁，缺失仅 warn 不阻断）")
```

在收据写盘前（`Receipt(...)` 构造前）追加解析（对齐 `_resolve_package` 容错口径）：

```python
    coverage = ""
    if args.coverage_file:
        try:
            cdata = json.loads(Path(args.coverage_file).read_text(encoding="utf-8"))
            if isinstance(cdata, dict):
                coverage = json.dumps(cdata, ensure_ascii=False,
                                      separators=(",", ":"))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"warn: --coverage-file 读取失败（不入收据）: {e}",
                  file=sys.stderr)
```

在 `Receipt(...)` 构造传 `coverage=coverage`。

- [ ] **Step 3: ws_verify_chain 加 --coverage 可选步**

在 `ws_verify_chain.py`：
1. `_CHAIN_STEPS` 不变（coverage 作为可选追加步，不入常链）；
2. `run_chain` 增参 `coverage: bool = False`，并在 `_run_chain_locked` 调用处透传；
3. `_run_chain_locked` 在 unit_test 步成功之后、acceptance 步之前追加：

```python
        if coverage and overall == "pass" and not fail_stop:
            t0m, t0 = time.monotonic(), time.time()
            cov_argv = [sys.executable,
                        str(_SCRIPT_DIR / "ws_coverage.py"),
                        "--product", product]
            if out:
                cov_argv += ["--out", out]
            cov_file = str(_CROSS_DEVICE_LOG / f"coverage-{suffix}.json")
            cov_argv += ["--result-file", cov_file]
            rc, canceled = _run_step(cov_argv, timeout_map["unit_test"])
            steps.append({"name": "coverage", "rc": rc, "start": t0,
                          "end": time.time(),
                          "dur_s": round(time.monotonic() - t0m, 3),
                          "canceled": canceled})
            chain_args["coverage_file"] = cov_file
```

4. `_build_report_argv` 增加透传：

```python
    if chain_args.get("coverage_file"):
        cmd += ["--coverage-file", chain_args["coverage_file"]]
```

5. `main` argparse 增加：

```python
    ap.add_argument("--coverage", action="store_true",
                    help="单测后采集覆盖率（ws_coverage；只记录不门禁）")
```

并透传给 `run_chain(... coverage=args.coverage)`。

- [ ] **Step 4: 写测试**

在 `test_ws_verify_chain.py` 追加：

```python
    def test_coverage_step_runs_after_unit_test(self):
        ctor, proc = _fake_popen(0)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      coverage=True, use_locks=False)
        names = _script_names(ctor.call_args_list)
        self.assertIn("ws_coverage.py", names)
        self.assertGreater(
            [i for i, n in enumerate(names) if n == "ws_coverage.py"][0],
            [i for i, n in enumerate(names) if n == "ws_upload_tests.py"][0])
```

- [ ] **Step 5: 运行受影响测试**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_coverage.py harness/skills/workspace-verify/tests/test_ws_report.py harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q 2>&1 | tail -3`
Expected: 全部 passed。

- [ ] **Step 6: 提交**

```bash
git add harness/skills/cross-device/lib/python/cdp_receipt.py harness/skills/workspace-verify/ws_report.py harness/skills/workspace-verify/ws_verify_chain.py harness/skills/workspace-verify/tests/
git commit -m "新增(harness): 收据 coverage 字段与 ws_verify_chain --coverage 可选步（覆盖率入证据）"
```

---

## Phase 5 — P1-B promote 审批独立性（修复 KI-20260907-001）

### Task 13: promote 审批独立校验（身份不等式 + 凭据外部化）

**Files:**
- Modify: `harness/skills/publish-main-base/baseline_register.py`
- Modify: `harness/skills/publish-main-base/tests/test_baseline_register.py`
- Modify: `.gitignore`
- Create: `harness/config/promote-approval.env.example`

- [ ] **Step 1: 写失败测试**

在 `harness/skills/publish-main-base/tests/test_baseline_register.py` 追加：

```python
    def test_promote_rejects_approver_same_as_operator(self):
        # KI-20260907-001：审批人恒等执行人 → 拒（身份不等式硬校验）
        from unittest import mock
        with mock.patch.object(br, "_collect_operator",
                               return_value="lechao <lechao@x.com>"), \
                mock.patch.object(br, "_read_approval_token",
                                  return_value="tok-abc"), \
                mock.patch.dict("os.environ",
                                {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}):
            rc = br.main(["promote", "--baseline-id", self.bid,
                          "--approved-by", "lechao <lechao@x.com>",
                          "--approval-token-file",
                          str(self.tmp / "approval.env")])
        self.assertNotEqual(rc, 0)

    def test_promote_rejects_token_mismatch(self):
        with mock.patch.object(br, "_collect_operator",
                               return_value="lechao <lechao@x.com>"), \
                mock.patch.object(br, "_read_approval_token",
                                  return_value="tok-abc"):
            rc = br.main(["promote", "--baseline-id", self.bid,
                          "--approved-by", "reviewer <r@x.com>",
                          "--approval-token-file",
                          str(self.tmp / "approval.env")])
        self.assertNotEqual(rc, 0)

    def test_promote_approver_diff_and_token_match_ok(self):
        # 不同审批人 + token 匹配 → 放行（回归既有 promote 成功路径）
        with mock.patch.object(br, "_collect_operator",
                               return_value="lechao <lechao@x.com>"), \
                mock.patch.object(br, "_read_approval_token",
                                  return_value="tok-abc"), \
                mock.patch.dict("os.environ",
                                {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}):
            rc = br.main(["promote", "--baseline-id", self.bid,
                          "--approved-by", "reviewer <r@x.com>",
                          "--approval-token-file",
                          str(self.tmp / "approval.env")])
        self.assertEqual(rc, 0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/skills/publish-main-base/tests/test_baseline_register.py -q 2>&1 | tail -5`
Expected: 新增 3 用例 FAIL（`AttributeError: _collect_operator` / `_read_approval_token` 不存在），且因身份校验未实现，`test_promote_approver_diff_and_token_match_ok` 目前可能直接成功（未校验），故前两用例是主要失败信号。

- [ ] **Step 3: 实现审批独立校验**

在 `baseline_register.py` 增加两个辅助函数（放在 `check_issues_gate` 之前）：

```python
# ── P1-B：promote 审批独立校验（修复 KI-20260907-001）────────────
# 业界对齐 SLSA 独立审批思想：审批不得自证。两道校验——
#   1) 身份不等式：--approved-by 审批人不得等于执行人 git 身份（收据
#      operator 同源采集）；
#   2) 审批凭据外部化：LC_PROMOTE_APPROVAL_TOKEN 环境变量须与
#      harness/config/promote-approval.env 预设值一致（评审人独立持有，
#      文件 gitignore 不入库，杜绝审批可自证）。


def _collect_operator() -> str:
    """执行人 git 身份（与 cdp_receipt._collect_operator 同源口径）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                               / "skills" / "cross-device" / "lib" / "python"))
        import cdp_receipt
        return cdp_receipt._collect_operator()
    except Exception:
        return "unknown"


def _norm_identity(s: str) -> str:
    """身份归一：'Name <email>' → 'name'; 小写去空白（比较用）。"""
    s = (s or "").strip()
    if "<" in s:
        s = s.split("<", 1)[0]
    return s.lower().strip()


def _read_approval_token(token_file: str | None = None) -> str:
    """读 promote-approval.env 预设 token（缺省
    harness/config/promote-approval.env）；不存在返回 ''。"""
    path = Path(token_file) if token_file else (
        Path(__file__).resolve().parents[2] / "harness" / "config"
        / "promote-approval.env")
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln.startswith("LC_PROMOTE_APPROVAL_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _check_approval_independence(approved_by: str,
                                 operator: str,
                                 token: str) -> tuple[bool, str]:
    """审批独立校验：返回 (ok, err)。"""
    if not (approved_by or "").strip():
        return False, "promote 必须传 --approved-by（审批凭据外部化）"
    if _norm_identity(approved_by) == _norm_identity(operator) \
            and operator.lower() != "unknown":
        return False, (f"审批人 {approved_by!r} 与执行人 {operator!r} 相同，"
                       "审批缺乏独立隔离（KI-20260907-001），拒绝 promote")
    provided = (token or "").strip()
    if not provided:
        return False, "缺 LC_PROMOTE_APPROVAL_TOKEN（审批凭据外部化失败）"
    # 占位符/尖括号一律拒（真实随机 token 不含 < >）
    if "<" in provided or ">" in provided:
        return False, "LC_PROMOTE_APPROVAL_TOKEN 为占位符，拒绝 promote"
    expected = _read_approval_token()
    if expected and provided != expected:
        return False, "LC_PROMOTE_APPROVAL_TOKEN 与 promote-approval.env 预设值不一致"
    return True, ""
```

在 `main` argparse 增加：

```python
    ap.add_argument("--approval-token-file", default="",
                    help="promote-approval.env 路径（测试/异地覆盖；缺省 "
                         "harness/config/promote-approval.env）")
```

在 promote 分支 `args.approved_by` 非空校验处（`baseline_register.py:561-566` 附近）改为调用辅助函数：

```python
                # P1-B：审批独立校验（身份不等式 + 凭据外部化，修复
                # KI-20260907-001）——替代原仅非空校验
                if not args.approved_by:
                    print("error: promote 必须传 --approved-by"
                          "（审批凭据外部化，不再回落默认常量）", file=sys.stderr)
                    return 1
                ok, aerr = _check_approval_independence(
                    args.approved_by, _collect_operator(),
                    os.environ.get("LC_PROMOTE_APPROVAL_TOKEN", ""))
                if not ok:
                    print(f"error: {aerr}", file=sys.stderr)
                    return 1
```

> 注：`_read_approval_token` 在 `_check_approval_independence` 内以缺省路径调用；`--approval-token-file` 仅供测试/异地覆盖（测试中直接 patch `_read_approval_token` 返回值，故该参数实际未用——保持接口为测试预留）。

- [ ] **Step 4: 创建 env 示例 + gitignore**

创建 `harness/config/promote-approval.env.example`：

```bash
# promote 审批凭据外部化（评审人独立持有；复制为 promote-approval.env，
# 该文件 gitignore 不入库）。值须为非占位符随机串。
LC_PROMOTE_APPROVAL_TOKEN=<PLACEHOLDER-请替换为随机token>
```

在 `.gitignore` 追加：

```
# promote 审批凭据（评审人独立持有，不入库）
/harness/config/promote-approval.env
```

- [ ] **Step 5: 更新既有 promote 测试（打桩 operator + env token）**

新增审批校验后，既有 promote 成功路径测试（`test_promote_requires_candidate`、`test_promote_creates_evidence_snapshot`、`test_promote_unknown_package_blocked` 等）会卡在审批门禁。在 `TestBaselineRegister` 增 helper 统一打桩：

```python
    def _promote_gate(self):
        """审批独立门禁打桩：operator 固定为执行人、env token 匹配预设。"""
        from unittest import mock
        return [
            mock.patch("baseline_register._collect_operator",
                       return_value="lechao <lechao@x.com>"),
            mock.patch("baseline_register._read_approval_token",
                       return_value="tok-abc"),
            mock.patch.dict("os.environ",
                            {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}),
        ]
```

对既有 promote 调用（`self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")`）逐一包 `_promote_gate()` 上下文：

```python
        gates = self._promote_gate()
        for g in gates:
            g.start()
        try:
            rc, out = self._run("promote", "--baseline-id", bid,
                                "--approved-by", "reviewer")
        finally:
            for g in gates:
                g.stop()
```

> 注意：审批人改传 `reviewer`（≠ 执行人 `lechao`），避免身份不等式误拒。

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest harness/skills/publish-main-base/tests/test_baseline_register.py -q 2>&1 | tail -3`
Expected: 全部 passed（含新增 3 用例 + 更新后的既有 promote 用例）。

- [ ] **Step 6: 更新证据模板说明**

`harness/config/baseline-evidence-template.yaml` 中 `approved_by` 注释追加一行：

```yaml
# approved_by: 独立审批人（须 ≠ 执行人 git 身份，且 LC_PROMOTE_APPROVAL_TOKEN
#   与 promote-approval.env 预设一致，KI-20260907-001 修复后强制）
```

- [ ] **Step 7: 提交**

```bash
git add harness/skills/publish-main-base/baseline_register.py harness/skills/publish-main-base/tests/test_baseline_register.py .gitignore harness/config/promote-approval.env.example harness/config/baseline-evidence-template.yaml
git commit -m "修复(harness): promote 审批独立校验（身份不等式+凭据外部化，闭环 KI-20260907-001）"
```

---

## Phase 6 — 全量验证与收尾

### Task 14: 全量自检 + 测试 + CI 契约核对

- [ ] **Step 1: ruff 全量**

Run: `ruff check harness/`
Expected: `All checks passed!`

- [ ] **Step 2: 内核 host 单测直跑**

Run: `python3 harness/lib/check_host_tests.py`
Expected: `OK: 内核 host 单测全部通过`，退出码 0。

- [ ] **Step 3: 自度量直跑**

Run: `python3 harness/lib/metrics.py --report`
Expected: `metrics_rc=0` 开头统计文本。

- [ ] **Step 4: 全量 harness 测试**

Run: `python3 -m pytest harness/ -q 2>&1 | tail -5`
Expected: 全部 passed（新增测试文件全部纳入）。

- [ ] **Step 5: 全量自检**

Run: `python3 harness/lib/selfcheck.py`
Expected: 输出含 12 个 rc 键全 0（`pytest_rc/refs_rc/config_rc/contract_rc/pyenv_rc/ioctl_rc/manifest_rc/discipline_rc/scan_rc/ruff_rc/host_rc/metrics_rc`）。

- [ ] **Step 6: CI 契约测试**

Run: `python3 -m pytest harness/lib/tests/test_workflow_ci.py -q 2>&1 | tail -3`
Expected: 全部 passed（CI 键集合与 REQUIRED_RC_KEYS 双向一致）。

- [ ] **Step 7: ws_verify_chain quick/coverage 冒烟**

Run: `python3 harness/skills/workspace-verify/ws_verify_chain.py --help`
Expected: 帮助含 `--quick` 与 `--coverage`。

- [ ] **Step 8: 推送 dev（按仓内 git-works-push 流程）**

```bash
git add -A
git commit -m "构建(harness): harness 业界对齐五件套落地（快检/lint/自度量/覆盖率/审批独立）"
git push origin dev
```

---

## Self-Review（计划对照规格）

- **P0-A 快检进 CI**：Task 5/6（host_rc 进自检+CI）、Task 7（host-tests 作业 + clang 静态检查）、Task 8（--quick）→ ✓
- **P0-B lint 门禁**：Task 1/2（ruff 配置+存量清零）、Task 3/4（check_ruff + ruff_rc 三处同步）→ ✓
- **P0-C 自度量**：Task 9（metrics.py）、Task 10（metrics_rc + flake_count 收据字段）→ ✓
- **P1-A 覆盖率**：Task 11（ws_coverage 三态）、Task 12（收据 coverage 字段 + --coverage 步）→ ✓
- **P1-B 审批独立**：Task 13（身份不等式 + 凭据外部化，闭环 KI-20260907-001）→ ✓
- **CDP-DOD-001**：ruff_rc/host_rc/metrics_rc 均有调用方（selfcheck/CI）、破坏即判红用例（Task 3/5/9 测试）、进 REQUIRED_RC_KEYS（Task 4/6/10）→ ✓
- **无占位符**：除 `promote-approval.env.example` 明确占位说明外，全部代码完整。

**执行注意**：Phase 1 Task 4 已修正为只加 ruff_rc（host_rc/metrics_rc 分别在 Task 6/10 加入），避免中间态 REQUIRED_RC_KEYS 引缺键自检红。各任务提交按 dev 分支 git-works-push 规范执行。
