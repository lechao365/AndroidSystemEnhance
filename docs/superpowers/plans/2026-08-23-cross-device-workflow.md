# 跨设备协同开发工作流实施计划（检视修订版 v2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 spec（docs/superpowers/specs/2026-08-23-cross-device-workflow-design.md）定义的 6 个 skill + CDP 契约 + data/verify 收据 + 规则修订，使 emit（远端）/ apply（本地）跨设备迭代流程可用。

**Architecture:** 方案 A——独立 skill + 仓内文件契约。`harness/skills/cross-device/` 内含共享契约解析器（cdp_parse.py）、diff 校验器、收据模块；6 个 skill 各自 SKILL.md + 脚本；`.opencode/command/` 薄入口。状态交换 = git 分支（dev/main）+ 仓内 `data/verify/`。repo 即 pack，无外部依赖。

**Tech Stack:** python3（标准库 + PyYAML——项目现有依赖，unittest 测试）、bash（自包含）、git、adb（网络/mDNS）。

**v2 修订说明：** 本版并入三路检视（真实代码集成核对 / 独立代码审查 / spec 覆盖检查）的 43 项修复：9 项 Critical（路径算术、正则 MULTILINE、sha 位数比较、bash 语法、API 对齐等）、8 项 High、22 项一致性、4 项 spec 微修（已同步落盘 spec）。

**参考文档（实施时必读）：**
- `harness/reference/build-reference.md` / `incremental-dev-reference.md`（编译/推送硬约束 BLD-001~012 / INC-001~010）
- `harness/rules/source-code-modify.md`（SRC 规则，Task 8.2 全面改写它）
- 现有 skill 模板：`harness/skills/sync-code-to-workspace/SKILL.md`（SKILL.md 格式）

**前置事实（已核实，v2 修正）：**
- `code/rpi5/{aosp,kernel}/modified/*.diff` 为标准 git diff；`new/` 全量文件；`others/` 独立维护。
- `sync_code_to_workspace.py`：argparse 变量名 `parser`；plan 文件路径变量 `plan_file`（str 类型）；`_apply_plan(plan: str) -> bool`；`_verify_after_apply(orig_plan: str) -> bool`（单 bool 返回）；`harness_exit(code: int)` **单参数**（先 `log_error()` 再 exit）；patch 根路径用 `_patch_root()`（无 PATCHS_ROOT 全局）；plan/apply 是**两次独立进程调用**（--auto 必须单进程闭环）；PyYAML 是 `_check_baseline_promoted()` 内的延迟导入（放宽前置后主路径不再依赖）。
- `sync_workspace_to_code.py`：`generate_manifest(patch_root: Path, check_only: bool, kernel_deletions: list, aosp_deletions: list)`；main() 中 `patch_root = profile_path("PATCHS_DIR")`（L475）；workspace 存在性检查在 L484-494（--gen-manifest-only 插入点在 L478 空行处）。
- `data/` 未被 .gitignore 忽略；`harness/log/` 被忽略。
- baseline-status.yaml：`baselines:` 列表 + 头部 8 行语义注释（**PyYAML 往返不保留注释，save() 必须手工保留头部**）。
- 项目无 pytest，用 `python3 -m unittest`。

**命名约定（全计划统一）：**
- 共享库：`harness/skills/cross-device/lib/python/{cdp_paths.py,cdp_parse.py,cdp_validate_patch.py,cdp_receipt.py,cdp_emit_precheck.py}`
- verify 辅助：`harness/skills/workspace-verify/{ws_adb_connect.py,ws_acceptance.py,ws_report.py}`（三者均有 CLI 入口）
- 退出码：0 成功 / 1 逻辑错误 / 3 参数·环境缺失 / 4 无操作；解析器专用 11/12/14/15/16/17（spec §4.3，**仅 17 在 apply 角色降级 WARN，16 双角色 blocking**）
- 收据 result 枚举：pass / fail / skip / revert；batch_id 前缀 `manual-`/`revert-` + **10 位时间戳（%y%m%d%H%M）**
- 所有 apply 类 SKILL.md 首行注明「仅限 apply 设备（本地 WSL2）运行」；emit SKILL 注明「仅限 emit 设备运行」

---

## 阶段 0：共享路径与收据模块

### Task 0.1: 共享路径模块 cdp_paths.py

**Files:**
- Create: `harness/skills/cross-device/lib/python/cdp_paths.py`
- Test: `harness/skills/cross-device/tests/test_cdp_paths.py`

- [ ] **Step 1: 写失败测试**

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths


class TestCdpPaths(unittest.TestCase):
    def setUp(self):
        # 全部用例走临时根，避免在真实仓库 mkdir data/verify 弄脏工作树
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def test_data_verify_dir_env_override(self):
        self.assertEqual(str(cdp_paths.data_verify_dir()),
                         os.path.join(self._tmp.name, "data", "verify"))

    def test_receipt_dir_mkdir(self):
        d = cdp_paths.data_verify_dir()
        self.assertTrue(d.is_dir())

    def test_cdp_parse_script_path_resolution(self):
        # 未设 CDP_PROJECT_ROOT 时基于包目录探测（只读校验，不 mkdir）。
        # 注：cdp_parse.py 由 Task 1.1 创建，此处只校验路径解析（父目录即本模块所在目录）。
        os.environ.pop("CDP_PROJECT_ROOT")
        p = cdp_paths.cdp_parse_script()
        self.assertEqual(p.name, "cdp_parse.py")
        self.assertTrue(p.parent.is_dir(), f"父目录应存在: {p.parent}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: FAIL（ModuleNotFoundError: cdp_paths）

- [ ] **Step 3: 实现**

```python
"""cross-device pack 共享路径解析。

规则：CDP_PROJECT_ROOT 环境变量可覆盖项目根；默认自动探测——本文件位于
harness/skills/cross-device/lib/python/，向上回退 3 级（parents[2]）即
cross-device 包目录，parents[4] 为项目根。仓内状态目录统一为
<project_root>/data/verify/（仅 apply 侧写；emit 侧只读传入显式路径）。
"""
import os
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parents[2]  # .../cross-device


def project_root() -> Path:
    root = os.environ.get("CDP_PROJECT_ROOT")
    if root:
        return Path(root)
    return _PACK_DIR.parents[2]  # cross-device -> skills -> harness -> 项目根


def data_verify_dir() -> Path:
    """apply 侧写收据用（会 mkdir）；emit 侧勿调用（用 project_root()/"data"/"verify" 只读）。"""
    d = project_root() / "data" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cdp_parse_script() -> Path:
    return _PACK_DIR / "lib" / "python" / "cdp_parse.py"
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: PASS（3 tests）

- [ ] **Step 5: 确认未污染真实仓**

Run: `git status --porcelain`
Expected: 无 `data/` 未跟踪目录（测试全部走 tempfile）

- [ ] **Step 6: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): 共享路径模块 cdp_paths（环境覆盖+自动探测，emit 侧只读安全）"
```

### Task 0.2: 收据模块 cdp_receipt.py（写/读/趋势/老化）

**Files:**
- Create: `harness/skills/cross-device/lib/python/cdp_receipt.py`
- Test: `harness/skills/cross-device/tests/test_cdp_receipt.py`

- [ ] **Step 1: 写失败测试**

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths
import cdp_receipt


def _mk_receipt(batch_id="abc123def456", result="pass"):
    return cdp_receipt.Receipt(
        schema_version=1,
        batch_id=batch_id,
        batch_base="111111111111",
        verified_commit="222222222222",
        verify_mode="board",
        result=result,
        build="pass",
        push_board="pass",
        acceptance="svc:lechao_lcview pass",
        elapsed_s=120,
        summary="验证通过",
    )


class TestReceipt(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._dir = cdp_paths.data_verify_dir()

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def test_write_and_read_roundtrip(self):
        r = _mk_receipt()
        p = cdp_receipt.write_receipt(r, "正文: CDP 原文 + 失败现场")
        self.assertTrue(p.name.endswith("-abc123def456.md"))
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.batch_id, "abc123def456")
        self.assertEqual(got.result, "pass")
        self.assertEqual(got.verified_commit, "222222222222")

    def test_body_fields_do_not_bleed(self):
        # 正文含 "- result: fail" 行不得覆盖头部字段（只解析 ## body 之前）
        r = _mk_receipt(result="pass")
        p = cdp_receipt.write_receipt(r, "## 现场\n- result: fail\n- batch_id: fake000000000")
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.result, "pass")
        self.assertEqual(got.batch_id, "abc123def456")

    def test_latest_returns_most_recent(self):
        cdp_receipt.write_receipt(_mk_receipt("aaa111111111", "fail"), "x")
        cdp_receipt.write_receipt(_mk_receipt("bbb222222222", "pass"), "y")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        self.assertEqual(latest.batch_id, "bbb222222222")

    def test_latest_ignores_trend_md(self):
        # trend.md 按文件名排序恒在时间戳文件之后，必须被排除
        cdp_receipt.write_receipt(_mk_receipt("ccc333333333", "pass"), "z")
        (self._dir / "trend.md").write_text(
            "2026-08-23 10:00:00 ccc333333333 pass build=pass x\n", encoding="utf-8")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        self.assertEqual(latest.batch_id, "ccc333333333")
        self.assertEqual(latest.result, "pass")

    def test_trend_append_and_read(self):
        cdp_receipt.append_trend("2026-08-23 10:00:00", "abc123def456", "pass",
                                 "build=pass board=pass acc=pass", "验证通过")
        line = cdp_receipt.read_trend_last(self._dir)
        self.assertIn("abc123def456", line)
        self.assertIn("pass", line)

    def test_prune_keeps_50_details_and_keeps_trend(self):
        for i in range(55):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        cdp_receipt.append_trend("2026-08-23 10:00:00", "batch54000000000",
                                 "pass", "build=pass x", "y")
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 50)
        self.assertTrue((self._dir / "trend.md").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: FAIL（ModuleNotFoundError: cdp_receipt）

- [ ] **Step 3: 实现**

```python
"""data/verify 收据模块：写详情、读详情、趋势行、老化保留。

收据文件: data/verify/<YYYYMMDD-HHMMSS>-<batch_id>.md（markdown key-value 头 + 正文）
趋势文件: data/verify/trend.md（每批一行，保留 _TREND_KEEP 行）
注意: trend.md 不属于详情（文件名排序恒在最后，读取/老化必须显式排除）。
"""
import datetime
import re
from pathlib import Path

from cdp_paths import data_verify_dir

_DETAIL_KEEP = 50
_TREND_KEEP = 50
# 多行模式：^$ 锚定每一行（缺 MULTILINE 会导致 from_text 全默认值）
_FIELD_RE = re.compile(r"^- (\w+): (.+)$", re.MULTILINE)

_FIELDS = [
    "schema_version", "batch_id", "batch_base", "verified_commit",
    "verify_mode", "result", "build", "push_board", "acceptance",
    "elapsed_s", "summary",
]


class Receipt:
    def __init__(self, schema_version=1, batch_id="", batch_base="",
                 verified_commit="", verify_mode="board", result="fail",
                 build="skip", push_board="skip", acceptance="", elapsed_s=0,
                 summary=""):
        self.schema_version = schema_version
        self.batch_id = batch_id
        self.batch_base = batch_base
        self.verified_commit = verified_commit
        self.verify_mode = verify_mode
        self.result = result
        self.build = build
        self.push_board = push_board
        self.acceptance = acceptance
        self.elapsed_s = elapsed_s
        self.summary = summary

    @classmethod
    def from_text(cls, text):
        # 只解析 "## body" 之前的头部，防止正文中的 "- key: value" 行污染字段
        header = text.split("\n## body", 1)[0]
        r = cls()
        for m in _FIELD_RE.finditer(header):
            key, val = m.group(1), m.group(2)
            if hasattr(r, key):
                if key in ("schema_version", "elapsed_s"):
                    setattr(r, key, int(val))
                else:
                    setattr(r, key, val)
        return r

    def header_lines(self):
        return "\n".join(f"- {f}: {getattr(self, f)}" for f in _FIELDS)


def _detail_files(verify_dir: Path):
    """详情文件列表（排除 trend.md），按文件名升序。"""
    return sorted(f for f in verify_dir.glob("*.md") if f.name != "trend.md")


def write_receipt(receipt, body_text):
    """写详情文件并老化，返回路径。"""
    d = data_verify_dir()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = d / f"{ts}-{receipt.batch_id}.md"
    content = receipt.header_lines() + "\n\n## body\n\n" + body_text.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    prune_details(d)
    return path


def read_receipt(path):
    return Receipt.from_text(Path(path).read_text(encoding="utf-8"))


def read_latest_receipt(verify_dir=None):
    """读最新详情（排除 trend.md）。无收据返回 None。"""
    d = verify_dir or data_verify_dir()
    files = _detail_files(d)
    if not files:
        return None
    return read_receipt(files[-1])


def append_trend(timestamp, batch_id, result, stage, summary):
    d = data_verify_dir()
    trend = d / "trend.md"
    line = f"{timestamp} {batch_id} {result} {stage} {summary}\n"
    with trend.open("a", encoding="utf-8") as f:
        f.write(line)
    lines = trend.read_text(encoding="utf-8").splitlines()
    if len(lines) > _TREND_KEEP:
        trend.write_text("\n".join(lines[-_TREND_KEEP:]) + "\n", encoding="utf-8")


def read_trend_last(verify_dir=None):
    d = verify_dir or data_verify_dir()
    trend = d / "trend.md"
    if not trend.exists():
        return ""
    lines = trend.read_text(encoding="utf-8").splitlines()
    return lines[-1] if lines else ""


def prune_details(verify_dir=None):
    """详情老化保留 _DETAIL_KEEP 份（trend.md 不计入配额）。"""
    d = verify_dir or data_verify_dir()
    files = _detail_files(d)
    for old in files[: max(0, len(files) - _DETAIL_KEEP)]:
        old.unlink()
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: PASS（6 tests）

- [ ] **Step 5: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): data/verify 收据模块（多行解析/头部隔离/trend 排除/老化不含 trend）"
```

---

## 阶段 1：CDP 契约解析器与 diff 校验器

### Task 1.1: CDP 契约解析器 cdp_parse.py

**Files:**
- Create: `harness/skills/cross-device/lib/python/cdp_parse.py`
- Create: `harness/skills/cross-device/docs/cdp-contract.md`
- Test: `harness/skills/cross-device/tests/test_cdp_parse.py`

- [ ] **Step 1: 写失败测试**

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_parse as cp

VALID_SV = """-sv base:1a2b3c4d5e6f
意图: 修复 lcview 空指针
验收: svc:lechao_lcview
方向: 检查 service.cpp 入口
"""

VALID_S = """-s base:1a2b3c4d5e6f
意图: 更新 README 映射表说明
验收: 无
方向: 补充新增文件条目描述
"""


class TestParse(unittest.TestCase):
    def test_parse_sv(self):
        b = cp.parse_batch(VALID_SV)
        self.assertEqual(b.mode, "sv")
        self.assertEqual(b.base, "1a2b3c4d5e6f")
        self.assertIn("lcview", b.intent)
        self.assertIn("svc:", b.acceptance)

    def test_parse_s(self):
        b = cp.parse_batch(VALID_S)
        self.assertEqual(b.mode, "s")
        self.assertEqual(b.acceptance, "无")

    def test_batch_id_deterministic(self):
        self.assertEqual(cp.batch_id_from_text(VALID_SV), cp.batch_id_from_text(VALID_SV))
        self.assertNotEqual(cp.batch_id_from_text(VALID_SV), cp.batch_id_from_text(VALID_S))

    def test_validate_ok(self):
        code, errs = cp.validate_batch(VALID_SV, role="emit")
        self.assertEqual(code, 0, errs)
        code, errs = cp.validate_batch(VALID_S, role="emit")
        self.assertEqual(code, 0, errs)

    def test_empty_batch(self):
        code, _ = cp.validate_batch("", role="emit")
        self.assertEqual(code, 12)

    def test_struct_first_line(self):
        # 首行结构错误（缺模式标记 / base 缺失）→ 11
        for bad in ["sv base:1a2b3c4d5e6f", "-sv", "-s 1a2b3c4d5e6f"]:
            code, _ = cp.validate_batch(
                bad + "\n意图: x\n验收: 无\n方向: y\n", role="emit")
            self.assertEqual(code, 11, bad)

    def test_missing_tags(self):
        text = "-sv base:1a2b3c4d5e6f\n意图: 只有意图\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 14)

    def test_bad_base(self):
        # 首行结构合法但 base 非 12hex → 15（MODE_RE 放宽后才可达）
        text = "-sv base:xyz\n意图: 修复 lcview 空指针问题\n验收: svc:lechao_lcview\n方向: 检查入口\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 15)

    def test_over_budget(self):
        text = "-s base:1a2b3c4d5e6f\n意图: " + "x" * 600 + "\n验收: 无\n方向: y\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 16)
        # 16 在 apply 角色同样 blocking（仅 17 降级）
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 16)

    def test_under_budget(self):
        text = "-s base:1a2b3c4d5e6f\n意图: a\n验收: 无\n方向: b\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 16)

    def test_sv_acceptance_rule(self):
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_s_acceptance_must_be_wu(self):
        text = VALID_S.replace("验收: 无", "验收: svc:xx")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_apply_role_softens_only_17(self):
        # validate_batch 恒返回原始码 17（降级由 main 统一处理）
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 17)

    def test_cli_apply_softened_warn_prefix(self):
        # apply 角色 17 降级：main 返回 0，且输出 warn: 前缀（不得 error:）
        import io
        import tempfile
        from contextlib import redirect_stdout
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        with tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False) as f:
            f.write(text)
            path = f.name
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cp.main(["--role", "apply", path])
        self.assertEqual(rc, 0)
        self.assertIn("warn:", buf.getvalue())
        self.assertNotIn("error:", buf.getvalue())

    def test_base_match(self):
        self.assertTrue(cp.base_matches(VALID_SV, "1a2b3c4d5e6f"))
        self.assertTrue(cp.base_matches(VALID_SV, "1A2B3C4D5E6F"))
        self.assertFalse(cp.base_matches(VALID_SV, "ffffffffffff"))

    def test_cli_missing_file_exit_3(self):
        # 批次文件不可读 → 3（契约表参数错误）
        self.assertEqual(cp.main(["--role", "emit", "/nonexistent.cdp"]), 3)

    def test_cli_expect_base_mismatch_exit_3(self):
        # base 不匹配本地 HEAD → 拒批 exit 3（spec §4.2/§8）
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False) as f:
            f.write(VALID_SV)
            path = f.name
        self.assertEqual(cp.main(["--role", "apply", "--expect-base",
                                  "ffffffffffff", path]), 3)
        self.assertEqual(cp.main(["--role", "apply", "--expect-base",
                                  "1a2b3c4d5e6f", path]), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: FAIL（ModuleNotFoundError: cdp_parse）

- [ ] **Step 3: 实现 cdp_parse.py**

```python
"""CDP 契约解析与校验（cross-device emit/apply 共用，仓内单份）。

格式（见 docs/cdp-contract.md，CDP-001 纪律：契约文档与解析器成对修改）：
  -s/-sv base:<12hex>
  意图: ...
  验收: ...   (-s 必须为「无」；-sv 必须非空且不得为「无」)
  方向: ...
退出码: 0 通过 / 3 参数错误·base 不匹配(--expect-base) / 11 结构错误 / 12 空批
       / 14 三标签缺失 / 15 base 非法 / 16 预算超限(>500 或 <50) / 17 验收规则违规
角色差异: validate_batch 恒返回原始判定码；降级（apply 仅对 17 → WARN）由
main() 依据 SOFT_ERRORS + role 统一处理（16 双角色 blocking）。
"""
import hashlib
import re
import sys
from dataclasses import dataclass

MIN_CHARS = 50
MAX_CHARS = 500
BASE_RE = re.compile(r"^[0-9a-fA-F]{12}$")
# 首行结构只约束「模式标记 + base: 字样」，base 值合法性交给 BASE_RE（保 15 可达）
MODE_RE = re.compile(r"^(-s|-sv)\s+base:\s*(\S+)\s*$")
TAG_RE = re.compile(r"^(意图|验收|方向):\s*(.*)$")

EXIT_OK = 0
EXIT_ARGS = 3
EXIT_STRUCT = 11
EXIT_EMPTY = 12
EXIT_NO_CONTRACT = 14
EXIT_BAD_BASE = 15
EXIT_BUDGET = 16
EXIT_ACCEPTANCE = 17

# 仅 17 在 apply 角色降级（spec §4.3）；16 不降级
SOFT_ERRORS = {EXIT_ACCEPTANCE}


@dataclass
class Batch:
    mode: str = ""        # "s" | "sv"
    base: str = ""
    intent: str = ""
    acceptance: str = ""
    direction: str = ""
    text: str = ""


def normalize_batch_text(text: str) -> str:
    """剥 BOM、逐行 strip、去空行、统一 LF。batch_id 与解析共用。"""
    text = text.lstrip("\ufeff")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def batch_id_from_text(text: str) -> str:
    return hashlib.sha256(normalize_batch_text(text).encode("utf-8")).hexdigest()[:12]


def parse_batch(text: str) -> Batch:
    b = Batch(text=text)
    norm = normalize_batch_text(text)
    if not norm:
        return b
    lines = norm.splitlines()
    m = MODE_RE.match(lines[0])
    if m:
        b.mode = m.group(1)[1:]  # "-sv" -> "sv", "-s" -> "s"
        b.base = m.group(2).lower()
        for ln in lines[1:]:
            t = TAG_RE.match(ln)
            if t:
                key, val = t.group(1), t.group(2).strip()
                if key == "意图":
                    b.intent = val
                elif key == "验收":
                    b.acceptance = val
                elif key == "方向":
                    b.direction = val
    return b


def validate_batch(text: str, role: str = "emit"):
    """返回 (exit_code, errors)。role=emit 全 blocking；apply 仅 17 降级。"""
    norm = normalize_batch_text(text)
    if not norm:
        return EXIT_EMPTY, ["空批次"]

    lines = norm.splitlines()
    if not MODE_RE.match(lines[0]):
        return EXIT_STRUCT, [f"首行必须为 -s/-sv base:<12hex>，实际: {lines[0]!r}"]

    b = parse_batch(norm)
    if not (b.intent and b.acceptance and b.direction):
        return EXIT_NO_CONTRACT, ["意图/验收/方向 三标签必填"]

    if not BASE_RE.match(b.base):
        return EXIT_BAD_BASE, [f"base 必须为 12 位 hex: {b.base!r}"]

    n = len(norm)
    if not (MIN_CHARS <= n <= MAX_CHARS):
        return EXIT_BUDGET, [f"预算 {MIN_CHARS}~{MAX_CHARS} 字符，实际 {n}"]

    if b.mode == "sv":
        if not b.acceptance or b.acceptance == "无":
            return EXIT_ACCEPTANCE, ["-sv 批次验收必须非空且不得为「无」"]
    else:
        if b.acceptance != "无":
            return EXIT_ACCEPTANCE, ["-s 批次验收必须为「无」"]

    return EXIT_OK, []


def base_matches(text: str, expect_head12: str) -> bool:
    """批次 base 是否与 apply 侧起始 HEAD（前 12 位）匹配（忽略大小写）。"""
    b = parse_batch(text)
    return bool(b.base) and bool(expect_head12) and \
        b.base.lower() == expect_head12.strip().lower()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: cdp_parse.py --role emit|apply [--expect-base <12hex>] <批次文件>")
        return 0
    # 手工解析参数：缺失参数统一 exit 3（argparse 默认 exit 2，不符合契约表）
    role, expect, path = "emit", None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--role" and i + 1 < len(argv):
            role = argv[i + 1]; i += 2; continue
        if a == "--expect-base" and i + 1 < len(argv):
            expect = argv[i + 1]; i += 2; continue
        if a.startswith("--"):
            print(f"error: 未知参数 {a}")
            return EXIT_ARGS
        if path is None:
            path = a; i += 1; continue
        print(f"error: 多余参数 {a}")
        return EXIT_ARGS
    if role not in ("emit", "apply") or path is None:
        print("error: 用法: cdp_parse.py --role emit|apply [--expect-base <12hex>] <批次文件>")
        return EXIT_ARGS
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"error: 批次文件不可读: {e}")
        return EXIT_ARGS

    code, errs = validate_batch(text, role=role)
    softened = code in SOFT_ERRORS and role == "apply"
    for e in errs:
        print(f"{'warn' if softened else 'error'}: {e}")
    if code != EXIT_OK and not softened:
        # 失败路径不打印 batch_id/mode（空批会打印空串误导上层）
        return code
    if expect is not None and not base_matches(text, expect):
        b = parse_batch(text)
        print(f"error: base 不匹配（批次 {b.base} != 本地 HEAD {expect.strip()}），整批拒绝")
        return EXIT_ARGS
    b = parse_batch(text)
    print(f"batch_id: {batch_id_from_text(text)}")
    print(f"mode: {b.mode} base: {b.base}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 写契约文档**

`harness/skills/cross-device/docs/cdp-contract.md`（SSOT，与 cdp_parse.py 成对修改，CDP-001）：

```markdown
# CDP 契约（cross-device prompt batch）

> **CDP-001**：本文档与 `../lib/python/cdp_parse.py` 必须成对修改，禁止单独改一边。

## 格式

    -s/-sv base:<12hex>
    意图: <这一轮要达成什么>
    验收: <判据>          (-s 必须为「无」；-sv 必须非空且不得为「无」)
    方向: <实施方向/约束>

## 规则

| 项 | 规则 |
|---|---|
| 模式 | `-s` 仅代码改动无上板验证；`-sv` 需上板验证 |
| base | 12 位 hex，= emit 产批时 origin/dev HEAD 前 12 位；apply 以 `--expect-base $(git rev-parse --short=12 HEAD)` 比对，不匹配整批拒绝（exit 3） |
| 三标签 | 必填各占一段 |
| 预算 | 总字符 50~500（含首行） |
| batch_id | 规范化文本（剥 BOM/strip/去空行/LF）sha256 前 12 位 |
| 验收标签 | 推荐格式（非强制）：`svc:<svc>` 服务运行 / `log:<kw>` logcat 命中 / `prop:<k>=<v>` / `file:<path>` 存在 / `cmd:"<shell>"`（含空格加引号）exit 0 / `boot` boot_completed；允许自由文本由 AI 判断 |

## 退出码

0 通过 / 3 参数错误·base 不匹配 / 11 结构错误 / 12 空批 / 14 三标签缺失 /
15 base 非法 / 16 预算超限 / 17 验收规则违规
（emit 全 blocking；apply 仅对 17 降级 WARN，16 双角色 blocking）
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: PASS（15 tests）

- [ ] **Step 6: 手工冒烟（CLI 端到端）**

```bash
cat > /tmp/opencode/sample.cdp <<'EOF'
-sv base:1a2b3c4d5e6f
意图: 修复 lcview 空指针
验收: svc:lechao_lcview
方向: 检查 service.cpp 入口
EOF
python3 harness/skills/cross-device/lib/python/cdp_parse.py --role emit /tmp/opencode/sample.cdp
echo "exit=$?"
python3 harness/skills/cross-device/lib/python/cdp_parse.py --role apply --expect-base ffffffffffff /tmp/opencode/sample.cdp
echo "exit=$?"
```
Expected: 第一条 exit 0 输出 batch_id/mode；第二条 exit 3 输出 base 不匹配

- [ ] **Step 7: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): CDP 契约解析器与契约文档（15 可达/17-only 降级/--expect-base 拒批）"
```

### Task 1.2: diff 格式校验器 cdp_validate_patch.py

**Files:**
- Create: `harness/skills/cross-device/lib/python/cdp_validate_patch.py`
- Test: `harness/skills/cross-device/tests/test_cdp_validate_patch.py`

- [ ] **Step 1: 写失败测试**

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_validate_patch as cv

GOOD = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,3 +10,5 @@ static int x;
 int y;
+
+/* new line */
"""

GOOD_NEW_FILE = """diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+first
+second
"""

GOOD_NO_NEWLINE = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,2 +10,2 @@ static int x;
-old
+new
\\ No newline at end of file
"""

BAD_MISSING_HUNK = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
no hunk here
"""

BAD_CONTEXT_LINE = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,3 +10,5 @@ static int x;
this line has no prefix
"""


class TestValidatePatch(unittest.TestCase):
    def _write(self, content):
        f = tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False,
                                        encoding="utf-8", newline="\n")
        f.write(content)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_good_modified(self):
        ok, errs = cv.validate_diff(self._write(GOOD))
        self.assertTrue(ok, errs)

    def test_good_new_file(self):
        # new file mode 行必须被接受（否则所有新建文件 diff 被误拒）
        ok, errs = cv.validate_diff(self._write(GOOD_NEW_FILE))
        self.assertTrue(ok, errs)

    def test_good_no_newline_marker(self):
        ok, errs = cv.validate_diff(self._write(GOOD_NO_NEWLINE))
        self.assertTrue(ok, errs)

    def test_missing_hunk(self):
        ok, errs = cv.validate_diff(self._write(BAD_MISSING_HUNK))
        self.assertFalse(ok)
        self.assertTrue(any("hunk" in e or "非法" in e for e in errs))

    def test_bad_line_prefix(self):
        ok, errs = cv.validate_diff(self._write(BAD_CONTEXT_LINE))
        self.assertFalse(ok)
        self.assertTrue(any("前缀" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: FAIL（ModuleNotFoundError: cdp_validate_patch）

- [ ] **Step 3: 实现**

```python
"""code/rpi5 modified/*.diff 格式校验器。

apply 编辑 .diff 后必须通过本校验：diff --git 头 / 元信息行 / hunk @@ /
行前缀(空格、+、-、反斜杠标记)合法性。防止 AI 编辑引入 diff 外新 context
导致 git apply 失配。
"""
import argparse
import re
import sys
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(.*)$")
# 显式枚举 git diff 全部元信息行形态（含 new file mode / rename / mode 变更等）
_META_RE = re.compile(
    r"^(diff --git |index |--- |\+\+\+ |@@ "
    r"|new file mode |deleted file mode "
    r"|old mode |new mode "
    r"|rename from |rename to "
    r"|similarity index |dissimilarity index "
    r"|copy from |copy to "
    r"|Binary files |GIT binary patch "
    r"|\\ No newline)"
)


def validate_diff(path):
    """返回 (ok, errors)。合法结构 + 每个 hunk 体行前缀必须为 ' ' / '+' / '-' / '\\'。"""
    errs = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return False, [f"文件不可读: {e}"]
    if not lines:
        return False, ["空文件"]
    if not lines[0].startswith("diff --git "):
        errs.append(f"首行必须为 diff --git 头: {lines[0]!r}")

    in_hunk = False
    for i, ln in enumerate(lines[1:], start=2):
        if _HUNK_RE.match(ln):
            in_hunk = True
            continue
        if in_hunk:
            if ln.startswith((" ", "+", "-")) or ln.startswith("\\"):
                continue
            if _META_RE.match(ln):
                in_hunk = False
                continue
            errs.append(f"L{i} hunk 体内行前缀非法（须 空格/+/-/\\）: {ln[:60]!r}")
            in_hunk = False
        else:
            if not _META_RE.match(ln):
                errs.append(f"L{i} 非法行（应为元信息行）: {ln[:60]!r}")
    return (not errs), errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=".diff 格式校验")
    ap.add_argument("files", nargs="+", help="diff 文件路径")
    args = ap.parse_args(argv)
    bad = 0
    for f in args.files:
        ok, errs = validate_diff(f)
        for e in errs:
            print(f"{f}: error: {e}")
        if not ok:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest discover -s harness/skills/cross-device/tests -v`
Expected: PASS（5 tests）

- [ ] **Step 5: 用仓内真实 diff 回归**

```bash
python3 harness/skills/cross-device/lib/python/cdp_validate_patch.py \
  code/rpi5/aosp/modified/device/brcm/rpi5/aosp_rpi5.mk.diff \
  code/rpi5/kernel/modified/drivers/usb/storage/usb.c.diff
echo "exit=$?"
```
Expected: exit 0（现存全部合法 diff 通过；若某真实 diff 报错，回头修 _META_RE 而非改 diff）

- [ ] **Step 6: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): modified/*.diff 格式校验器（全元信息行枚举+新文件/换行标记支持）"
```

---

## 阶段 2：旧脚本改造（--auto 与 manifest 抽取）

### Task 2.1: sync_code_to_workspace.py 新增 --auto 模式（含交互前置放宽）

**Files:**
- Modify: `harness/skills/sync-code-to-workspace/sync_code_to_workspace.py`

- [ ] **Step 1: 阅读现状确认改造点（必做，后续代码以真实签名为准）**

Read: `sync_code_to_workspace.py` 的 `main()`（L842-921）、`_git_check` 封装（L116-151）、`_artifact_path`（L85-88）、plan 分支与 apply 分支的现有调用形态。
重点确认：`_gen_plan` 的真实调用形态与返回值（现有 plan 分支怎么调）、`_git_check` 参数签名。

- [ ] **Step 2: argparse 增加 --auto（变量名 parser）**

在现有三个 `parser.add_argument` 之后追加：

```python
    parser.add_argument("--auto", action="store_true",
                        help="自动模式：生成计划→全选→执行→落盘校验单进程闭环；"
                             "plan 为空视为成功 exit 0（workspace-verify 内部使用）")
```

- [ ] **Step 3: 前置校验放宽（交互与 --auto 一致，spec §3.1）**

将 L870-872 的 `_check_baseline_promoted()` 检查块整体替换为（真相源放宽为 dev/main HEAD；`_check_baseline_promoted` 函数保留不删，供历史参考）：

```python
    # 前置：真相源为 code 仓 dev/main HEAD（spec §3.1：交互模式与 --auto 一致放宽，
    # 不再要求 promoted baseline；恢复真相源约束由 SRC-004 在规则层表达）
    if not _git_check("rev-parse", "--verify", "HEAD", cwd=str(_patch_root())):
        log_error("code 仓无 HEAD，无法以 dev/main 为真相源")
        harness_exit(3)
```

（注：`_git_check` 的真实签名以 Step 1 核实为准——封装于 L116-151，调用形态参考现有
`_git_check("cat-file", "-e", ...)` 用法；若参数顺序不同按实际调整。）

- [ ] **Step 4: --auto 单进程闭环分支**

在上述前置校验之后、现有 mode 三选一逻辑之前插入（plan/apply 本是两次进程调用，
--auto 必须单进程完成 gen→全选→apply→verify）：

```python
    if args.auto:
        plan_file = args.plan_file or _artifact_path("auto_plan.tsv")
        if not _gen_plan(plan_file):
            log_error("auto: 计划生成失败")
            harness_exit(3)
        _select_all(plan_file)
        plan_lines = [l for l in Path(plan_file).read_text(encoding="utf-8").splitlines()
                      if l and l[0] in "+-"]
        if not plan_lines:
            log_info("auto: plan 为空，code 与 workspace 一致，无需同步")
            harness_exit(0)
        if not _apply_plan(plan_file):
            log_error("auto: apply 失败")
            harness_exit(1)
        ok = _verify_after_apply(plan_file)
        harness_exit(0 if ok else 1)
```

并在模块函数区（如 `_apply_plan` 之前）新增辅助函数：

```python
def _select_all(plan_file: str) -> None:
    """把 plan 中所有 '-' 标记行改为 '+'（全选），供 --auto 模式使用。"""
    p = Path(plan_file)
    lines = p.read_text(encoding="utf-8").splitlines()
    out = [ln if ln.startswith(("#", "+")) else ("+" + ln[1:]) for ln in lines]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
```

（注：`_gen_plan(plan_file)` 的真实签名/返回值以 Step 1 核实为准，对齐现有 plan
分支的调用形态；`_apply_plan(plan: str) -> bool`、`_verify_after_apply(plan: str)
-> bool` 已核实。）

- [ ] **Step 5: plan 为空时交互模式语义保持（不改）**

现有 L884-890 交互模式 plan 为空 `harness_exit(4)` 保持不变（--auto 分支已在内部
提前返回 exit 0，不经过此处）。

- [ ] **Step 6: 冒烟验证（不破坏现有行为）**

```bash
python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --check-only
echo "exit=$?"
```
Expected: 输出计划预览（行为不变）；无 workspace 环境时 exit 3 属正常。有 workspace
环境时可加跑 `--auto` 观察"plan 为空 exit 0"或全选执行日志。

- [ ] **Step 7: 提交**

```bash
git add harness/skills/sync-code-to-workspace/sync_code_to_workspace.py
git commit -m "新增(sync-code-to-workspace): --auto 单进程闭环 + 前置放宽为 dev/main HEAD（spec §3.1）"
```

### Task 2.2: generate_manifest 抽取独立可调

**Files:**
- Modify: `harness/skills/sync-workspace-to-code/sync_workspace_to_code.py`

- [ ] **Step 1: 加 `--gen-manifest-only` 入口**

在 argparse（变量名 `parser`）追加：

```python
    parser.add_argument("--gen-manifest-only", action="store_true",
                        help="仅重生成 code/rpi5/manifest.yaml（deletions 传空），"
                             "供 cross-device-apply 使用")
```

在 `main()` 的 L478 空行处（`patch_root = profile_path("PATCHS_DIR")` 赋值之后、
L484 workspace 存在性检查之前）插入：

```python
    if args.gen_manifest_only:
        generate_manifest(patch_root, check_only=args.check_only,
                          kernel_deletions=[], aosp_deletions=[])
        harness_exit(0)
```

（签名已核实：`generate_manifest(patch_root: Path, check_only: bool,
kernel_deletions: list, aosp_deletions: list)`，L381-383。）

- [ ] **Step 2: 冒烟验证**

```bash
python3 harness/skills/sync-workspace-to-code/sync_workspace_to_code.py --gen-manifest-only --check-only
echo "exit=$?"
```
Expected: exit 0（--check-only 不写入；无 workspace 环境也能执行——插入点在存在性检查之前）

- [ ] **Step 3: 提交**

```bash
git add harness/skills/sync-workspace-to-code/sync_workspace_to_code.py
git commit -m "新增(sync-workspace-to-code): --gen-manifest-only 入口（manifest 重生成独立可调，deletions 空）"
```

---

## 阶段 3：workspace-verify skill

### Task 3.1: adb 连接子模块 ws_adb_connect.py（含 CLI）

**Files:**
- Create: `harness/skills/workspace-verify/ws_adb_connect.py`
- Test: `harness/skills/workspace-verify/tests/test_ws_adb_connect.py`

- [ ] **Step 1: 写失败测试**

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_adb_connect as ac


class TestCmdBuild(unittest.TestCase):
    def test_connect_static(self):
        self.assertEqual(ac.build_connect_cmd("10.0.0.5:5555"),
                         ["adb", "connect", "10.0.0.5:5555"])

    def test_exec_returns_exit_code_tagged(self):
        joined = " ".join(ac.build_exec_cmd("getprop ro.build.version"))
        self.assertIn("__LE_EXIT_CODE__", joined)

    def test_logcat_tail(self):
        joined = " ".join(ac.build_logcat_cmd("lechao", tail=200))
        self.assertIn("-d", joined)
        self.assertIn("200", joined)

    def test_parse_devices_states(self):
        out = ("List of devices attached\n"
               "192.168.1.5:5555\tdevice\n"
               "10.0.0.9:5555\toffline\n"
               "rp5.local:5555\tunauthorized\n")
        d = ac.parse_devices(out)
        self.assertEqual(d["192.168.1.5:5555"], "device")
        self.assertEqual(d["10.0.0.9:5555"], "offline")
        self.assertNotIn("List of", d)

    def test_is_online_by_full_serial_state(self):
        # 只有 serial 全匹配且 state==device 才在线（offline/unauthorized/子串不算）
        self.assertTrue(ac._state_online("192.168.1.5:5555",
                                         "List of devices attached\n192.168.1.5:5555\tdevice\n"))
        self.assertFalse(ac._state_online("192.168.1.5:5555",
                                          "List of devices attached\n192.168.1.5:5555\toffline\n"))
        # 子串不得误配（10.0.0.5 不能命中 10.0.0.50）
        self.assertFalse(ac._state_online("10.0.0.5:5555",
                                          "List of devices attached\n10.0.0.50:5555\tdevice\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/workspace-verify/tests -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
"""adb 连接子模块（workspace-verify 自包含，不依赖外部 skill）。

连接策略：mDNS 发现（adb mdns services，输出 3 列，endpoint 为最后一列
ip:port）→ 静态 fallback（默认 rp5.local:5555，LC_VERIFY_ADB_HOST/PORT 覆盖）。
在线判定：adb devices 两列制表符，serial 全匹配且 state == "device"。

CLI:
  ws_adb_connect.py ensure                     # 连接并输出 endpoint（失败 exit 1）
  ws_adb_connect.py devices                    # adb devices 原样输出
  ws_adb_connect.py exec --cmd "<shell>"       # 执行命令，末行输出 exit_code: N
  ws_adb_connect.py logcat [--filter f] [--tail N]
"""
import argparse
import json
import os
import re
import subprocess

_EXEC_TAG_RE = re.compile(r"__LE_EXIT_CODE__=(\d+)\s*$", re.MULTILINE)


def adb_bin():
    return os.environ.get("LC_VERIFY_ADB_BIN", "adb")


def host_port():
    h = os.environ.get("LC_VERIFY_ADB_HOST", "rp5.local")
    p = os.environ.get("LC_VERIFY_ADB_PORT", "5555")
    return f"{h}:{p}"


def build_connect_cmd(endpoint=None):
    return [adb_bin(), "connect", endpoint or host_port()]


def mdns_discover():
    """返回 endpoint 列表（每行最后一列为 ip:port）；mDNS 不可用返回空列表。"""
    try:
        r = subprocess.run([adb_bin(), "mdns", "services"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
        eps = []
        for ln in r.stdout.splitlines():
            if "_adb" in ln and "._tcp" in ln:
                parts = ln.split()
                if len(parts) >= 3 and (":" in parts[-1]):
                    eps.append(parts[-1])
        return eps
    except (OSError, subprocess.TimeoutExpired):
        return []


def parse_devices(text):
    """解析 adb devices 输出为 {serial: state}。"""
    out = {}
    for ln in text.splitlines()[1:]:
        if "\t" in ln:
            serial, state = ln.split("\t", 1)
            out[serial.strip()] = state.strip()
    return out


def _state_online(endpoint, devices_stdout):
    return parse_devices(devices_stdout).get(endpoint) == "device"


def _is_online(endpoint):
    try:
        r = subprocess.run([adb_bin(), "devices"], capture_output=True,
                           text=True, timeout=10)
        return _state_online(endpoint, r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_connected():
    """mDNS 优先逐个尝试，失败回退静态。返回在线 endpoint 或 None。"""
    for ep in mdns_discover():
        try:
            subprocess.run(build_connect_cmd(ep), capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if _is_online(ep):
            return ep
    ep = host_port()
    try:
        subprocess.run(build_connect_cmd(ep), capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ep if _is_online(ep) else None


def build_exec_cmd(cmd):
    """exec 命令：输出末尾附 __LE_EXIT_CODE__=<n> 以便解析退出码。"""
    return [adb_bin(), "shell", f"{cmd}; echo __LE_EXIT_CODE__=$?"]


def build_logcat_cmd(filter_expr=None, tail=200):
    cmd = [adb_bin(), "logcat", "-d"]
    if filter_expr:
        cmd += ["-s", filter_expr]
    cmd += ["-t", str(tail)]
    return cmd


def parse_exec_output(text):
    """解析 exec 输出，返回 (stdout_body, exit_code)。"""
    m = _EXEC_TAG_RE.search(text)
    if not m:
        return text, None
    body = text[: m.start()].rstrip()
    return body, int(m.group(1))


def main(argv=None):
    ap = argparse.ArgumentParser(description="adb 连接工具（mDNS→静态 fallback）")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("ensure")
    sub.add_parser("devices")
    p_exec = sub.add_parser("exec")
    p_exec.add_argument("--cmd", dest="shell_cmd", required=True)
    p_exec.add_argument("--timeout", type=int, default=60)
    p_log = sub.add_parser("logcat")
    p_log.add_argument("--filter")
    p_log.add_argument("--tail", type=int, default=200)
    args = ap.parse_args(argv)

    if args.action == "ensure":
        ep = ensure_connected()
        print(ep if ep else json.dumps(
            {"error": "设备不可达（mDNS 与静态 fallback 均失败）"},
            ensure_ascii=False))
        return 0 if ep else 1
    if args.action == "devices":
        r = subprocess.run([adb_bin(), "devices"], capture_output=True,
                           text=True, timeout=10)
        print(r.stdout)
        return 0
    if args.action == "exec":
        try:
            r = subprocess.run(build_exec_cmd(args.shell_cmd),
                               capture_output=True, text=True,
                               timeout=args.timeout)
            body, code = parse_exec_output(r.stdout)
            print(body)
            print(f"exit_code: {code}")
            return 0
        except subprocess.TimeoutExpired:
            print("error: exec 超时")
            return 1
    if args.action == "logcat":
        r = subprocess.run(build_logcat_cmd(args.filter, args.tail),
                           capture_output=True, text=True, timeout=60)
        print(r.stdout)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest discover -s harness/skills/workspace-verify/tests -v`
Expected: PASS（5 tests）

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/
git commit -m "新增(workspace-verify): adb 连接子模块（mDNS 3列解析/state=device 判定/CLI ensure/exec/logcat）"
```

### Task 3.2: 验收标签执行器 ws_acceptance.py（含 CLI）

**Files:**
- Create: `harness/skills/workspace-verify/ws_acceptance.py`
- Test: `harness/skills/workspace-verify/tests/test_ws_acceptance.py`

- [ ] **Step 1: 写失败测试**

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_acceptance as wa


class TestParseAcceptance(unittest.TestCase):
    def test_parse_tags(self):
        tags = wa.parse_acceptance("svc:lechao_lcview log:ERROR prop:a=b file:/data/x")
        self.assertEqual(tags, ["svc:lechao_lcview", "log:ERROR",
                                "prop:a=b", "file:/data/x"])

    def test_cmd_with_spaces_quoted(self):
        # cmd 含空格必须用引号包裹且整体保留
        tags = wa.parse_acceptance('cmd:"/system/bin/usb-verify --version"')
        self.assertEqual(tags, ['cmd:"/system/bin/usb-verify --version"'])

    def test_boot_bare_word(self):
        tags = wa.parse_acceptance("boot svc:lechao_lcview")
        self.assertEqual(tags, ["boot", "svc:lechao_lcview"])

    def test_free_text_single(self):
        tags = wa.parse_acceptance("设备能正常播放音频")
        self.assertEqual(tags, ["设备能正常播放音频"])

    def test_split_kind(self):
        self.assertEqual(wa.split_tag("svc:lechao_lcview"), ("svc", "lechao_lcview"))
        self.assertEqual(wa.split_tag("boot"), ("boot", ""))
        self.assertEqual(wa.split_tag('cmd:"a b"'), ("cmd", "a b"))
        self.assertEqual(wa.split_tag("自由文本"), ("text", "自由文本"))

    def test_free_text_returns_ai(self):
        # 自由文本返回 "ai"（交 verify AI 判定），不是 unknown
        status, _ = wa.execute_tag("设备正常", adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "ai")

    def test_overall_ai_only(self):
        overall, items = wa.run_acceptance(
            "设备正常", adb_exec=lambda c: ("", 0), adb_logcat=lambda k: "")
        self.assertEqual(overall, "ai")
        self.assertEqual(items[0]["status"], "ai")

    def test_overall_mixed(self):
        acc = 'boot cmd:"true"'
        overall, items = wa.run_acceptance(
            acc,
            adb_exec=lambda c: ("1", 0) if "boot_completed" in c else ("", 0),
            adb_logcat=lambda k: "")
        self.assertEqual(overall, "pass")
        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m unittest discover -s harness/skills/workspace-verify/tests -v`
Expected: FAIL（ModuleNotFoundError: ws_acceptance）

- [ ] **Step 3: 实现**

```python
"""验收标签解析与自动执行（含 CLI）。

标签语法（推荐格式）：svc:<svc> / log:<kw> / prop:<k>=<v> / file:<path> /
cmd:"<含空格的 shell>" 或 cmd:<无空格> / boot（裸词）；其余内容视为自由文本
（status='ai'，由 verify AI 现场判定）。
overall 语义：存在自动项时全 pass 才 pass、任一 fail 即 fail；
仅自由文本时返回 'ai'（由 AI 判定后覆盖）。

CLI:
  ws_acceptance.py run --acceptance "<验收文本>"
    → 内部 ensure_connected，逐项执行，输出 JSON，exit 0（fail 时 exit 1）
"""
import argparse
import json
import re
import subprocess

# cmd 支持引号包裹（含空格）；boot 为裸词；其余标签不含空格
_TAG_RE = re.compile(r'(?:svc|log|prop|file):\S+|cmd:(?:"[^"]*"|\S+)|\bboot\b')


def parse_acceptance(text):
    """提取标签列表；无任何标签则整段视为单条自由文本。"""
    tags = _TAG_RE.findall(text or "")
    if not tags and (text or "").strip():
        return [text.strip()]
    return tags


def split_tag(tag):
    if tag == "boot":
        return "boot", ""
    if ":" in tag:
        kind, payload = tag.split(":", 1)
        if kind == "cmd" and payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        return kind, payload
    return "text", tag


def execute_tag(tag, adb_exec, adb_logcat):
    """adb_exec(cmd)->(body, exit_code)；adb_logcat()->str。返回 (status, detail)。

    status: pass | fail | ai（自由文本由 AI 判定）
    """
    kind, payload = split_tag(tag)
    if kind == "svc":
        body, code = adb_exec(f"getprop init.svc.{payload}")
        return ("pass" if code == 0 and body.strip() == "running" else "fail",
                f"init.svc.{payload}={body.strip()!r} exit={code}")
    if kind == "log":
        out = adb_logcat()
        return ("pass" if payload in out else "fail",
                f"logcat 命中 {len(out)} 字符")
    if kind == "prop":
        k, _, v = payload.partition("=")
        body, code = adb_exec(f"getprop {k}")
        return ("pass" if body.strip() == v else "fail",
                f"{k}={body.strip()!r} 期望={v}")
    if kind == "file":
        body, code = adb_exec(f"ls -la {payload}")
        return ("pass" if code == 0 else "fail", body.strip()[:200])
    if kind == "cmd":
        body, code = adb_exec(payload)
        return ("pass" if code == 0 else "fail", body.strip()[:200])
    if kind == "boot":
        body, code = adb_exec("getprop sys.boot_completed")
        return ("pass" if code == 0 and body.strip() == "1" else "fail",
                f"sys.boot_completed={body.strip()!r}")
    # 自由文本：交 AI 判定
    return "ai", payload


def run_acceptance(acceptance_text, adb_exec, adb_logcat):
    """执行全部条目，返回 (overall, items)。overall ∈ pass|fail|ai。"""
    items = []
    for tag in parse_acceptance(acceptance_text):
        status, detail = execute_tag(tag, adb_exec, adb_logcat)
        items.append({"tag": tag, "status": status, "detail": detail})
    auto = [i for i in items if i["status"] in ("pass", "fail")]
    if not auto:
        return "ai", items
    overall = "pass" if all(i["status"] == "pass" for i in auto) else "fail"
    return overall, items


def main(argv=None):
    import ws_adb_connect as ac
    ap = argparse.ArgumentParser(description="验收执行器")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("run")
    p.add_argument("--acceptance", required=True, help="验收文本（含标签）")
    args = ap.parse_args(argv)

    ep = ac.ensure_connected()
    if not ep:
        print(json.dumps({"overall": "fail", "error": "设备不可达",
                          "items": []}, ensure_ascii=False))
        return 1

    def adb_exec(cmd):
        try:
            r = subprocess.run(ac.build_exec_cmd(cmd), capture_output=True,
                               text=True, timeout=60)
            return ac.parse_exec_output(r.stdout)
        except subprocess.TimeoutExpired:
            return "", None

    def adb_logcat():
        try:
            r = subprocess.run(ac.build_logcat_cmd(None, 5000),
                               capture_output=True, text=True, timeout=60)
            return r.stdout
        except subprocess.TimeoutExpired:
            return ""

    overall, items = run_acceptance(args.acceptance, adb_exec, adb_logcat)
    print(json.dumps({"overall": overall, "items": items}, ensure_ascii=False,
                     indent=2))
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m unittest discover -s harness/skills/workspace-verify/tests -v`
Expected: PASS（8 tests）

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/
git commit -m "新增(workspace-verify): 验收执行器（引号 cmd/boot 裸词/自由文本 ai 语义/run CLI）"
```

### Task 3.3: 收据落盘 ws_report.py

**Files:**
- Create: `harness/skills/workspace-verify/ws_report.py`

- [ ] **Step 1: 实现**

```python
"""verify 收据落盘：封装 cdp_receipt，按 verify 阶段汇总写 data/verify/。

用法（无子命令）：
  模式 A（apply 拉起，随批次）:
    ws_report.py --batch-file <cdp> [--target <12hex起点HEAD>] \
        --result pass|fail|skip --build ... --board ... \
        --acceptance "<逐项结果>" --elapsed <秒> --summary "<一句话>" \
        [--body <正文文件>（CDP 原文+失败现场，必传见 SKILL）]
  模式 B（独立触发）:
    ws_report.py --target <12hex|dev|main> [--prefix manual|revert] \
        --result ... （同上；batch_id = <prefix>-<8位时间戳>）
"""
import argparse
import re
import sys
import time
from pathlib import Path

# 本文件位于 harness/skills/workspace-verify/，parents[1] = harness/skills
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_parse import batch_id_from_text, parse_batch  # noqa: E402
from cdp_receipt import Receipt, append_trend, write_receipt  # noqa: E402


def _sanitize(text: str) -> str:
    """简单脱敏：家目录绝对路径 → ~（Windows 用户目录同理）。"""
    text = re.sub(r"/home/[A-Za-z0-9_.-]+", "~", text)
    text = re.sub(r"[A-Za-z]:\\+Users\\+[A-Za-z0-9_.-]+", "~", text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify 收据落盘")
    ap.add_argument("--batch-file", help="CDP 批次文件（模式 A）")
    ap.add_argument("--target", help="验证目标：12hex commit（模式 B 亦可用 dev/main 描述）")
    ap.add_argument("--prefix", choices=["manual", "revert"], default="manual",
                    help="模式 B 的 batch_id 前缀（revert 恢复验证用 revert）")
    ap.add_argument("--result", choices=["pass", "fail", "skip"], required=True)
    ap.add_argument("--build", choices=["pass", "fail", "skip"], default="skip")
    ap.add_argument("--board", choices=["pass", "fail", "skip"], default="skip")
    ap.add_argument("--acceptance", default="")
    ap.add_argument("--elapsed", type=int, default=0)
    ap.add_argument("--summary", default="")
    ap.add_argument("--body", help="正文文件路径（CDP 原文/失败现场），经脱敏写入")
    args = ap.parse_args(argv)

    if args.batch_file:
        text = Path(args.batch_file).read_text(encoding="utf-8")
        b = parse_batch(text)
        batch_id = batch_id_from_text(text)
        batch_base = b.base
        verify_mode = "board" if b.mode == "sv" else "none"
        # verified_commit = 验证起点 HEAD（= 该批 commit 的 parent）；
        # apply 链路中 HEAD 未动（编辑未提交），故等于 base；显式 --target 优先
        verified = args.target or b.base
    else:
        batch_id = f"{args.prefix}-{time.strftime('%y%m%d%H%M')}"
        batch_base = args.target or ""
        verify_mode = "board"
        verified = args.target or ""

    body = ""
    if args.body and Path(args.body).is_file():
        body = _sanitize(Path(args.body).read_text(encoding="utf-8"))

    r = Receipt(batch_id=batch_id, batch_base=batch_base,
                verified_commit=verified,
                verify_mode=verify_mode, result=args.result,
                build=args.build, push_board=args.board,
                acceptance=args.acceptance, elapsed_s=args.elapsed,
                summary=args.summary)
    path = write_receipt(r, body or args.summary)
    append_trend(time.strftime("%Y-%m-%d %H:%M:%S"), batch_id, args.result,
                 f"build={args.build} board={args.board} "
                 f"acc={args.acceptance.splitlines()[0][:40] if args.acceptance else '-'}",
                 args.summary[:40])
    print(f"receipt: {path}")
    print(f"batch_id: {batch_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 冒烟（模式 A + 模式 B）**

```bash
cat > /tmp/opencode/sample.cdp <<'EOF'
-sv base:1a2b3c4d5e6f
意图: 修复 lcview 空指针
验收: svc:lechao_lcview
方向: 检查 service.cpp 入口
EOF
python3 harness/skills/workspace-verify/ws_report.py --batch-file /tmp/opencode/sample.cdp \
  --result pass --build pass --board pass --acceptance 'svc:lechao_lcview pass' \
  --elapsed 120 --summary 验证通过
python3 harness/skills/workspace-verify/ws_report.py --prefix revert --result fail \
  --build fail --board skip --summary 'revert 后恢复验证'
tail -2 data/verify/trend.md
```
Expected: 两次输出 receipt 路径与 batch_id（第二次为 `revert-XXXXXXXX` 8 位时间戳）；trend 末两行对应

- [ ] **Step 3: 确认解析回读正确（防 MULTI-LINE 回归）**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, "harness/skills/cross-device/lib/python")
from cdp_receipt import read_latest_receipt
r = read_latest_receipt()
assert r.batch_id.startswith("revert-"), r.batch_id
assert r.result == "fail", r.result
print("roundtrip ok:", r.batch_id, r.result)
EOF
```
Expected: `roundtrip ok: revert-... fail`

- [ ] **Step 4: 提交**

```bash
git add harness/skills/workspace-verify/
git commit -m "新增(workspace-verify): 收据落盘 ws_report（模式A/B、revert 前缀、脱敏、8位时间戳）"
```

### Task 3.4: workspace-verify SKILL.md

**Files:**
- Create: `harness/skills/workspace-verify/SKILL.md`

- [ ] **Step 1: 编写 SKILL.md**（frontmatter 按现有模板）

```markdown
---
name: workspace-verify
description: code→workspace 同步、增量编译、adb 增量推送、验收执行并写 data/verify 收据（cross-device-apply 拉起或独立触发）。
no_commit: true
stages:
  - research: "读批次/目标、判定影响面"
  - plan: "AI 制定编译推送与验收计划"
  - code: "执行同步/编译/推送/验收/自愈"
  - review: "落盘收据并汇总"
---
# workspace-verify

> **仅限 apply 设备（本地 WSL2）运行**（需 workspace 与开发板访问）。

核心语义：把 code/ 当前状态（含未提交改动）同步到 workspace 编译，增量推送上板，
按验收标签/自由文本判定结果，落盘 data/verify 收据；失败走自愈（上限 3 次）。
## Trigger（触发条件）
- cross-device-apply 拉起（模式 A：--batch-file）
- 独立触发（模式 B：--target <12hex|dev|main>，如 revert 后恢复验证；
  模式 B 默认验收自动追加 boot 标签——设备存活是恢复的最低判据）
## Preconditions（前置条件）
- 本仓 dev/main 分支存在；KERNEL_WS/AOSP_WS 可访问（paths.conf）
- 设备可达（网络 adb；不可达时走串口诊断，见 Failure）
- 高危动作（整卡刷写、boot 分区 dd）必须人工确认
## Inputs（输入）
- 模式 A：批次文件路径（验收标签从批次解析）；模式 B：目标 commit + 验收文本
## Human confirmation gates（人工确认门）
- 仅整卡刷写 / boot 分区 dd 需确认；其余零确认
## Outputs / artifacts（输出/产物）
- data/verify/<时间戳>-<batch_id>.md + trend.md（只落盘，不 commit——由 git-works-push 随批统一提交）
- harness/log/workspace-verify/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- code→workspace 同步失败：verify 中止，收据 result=fail（build=fail board=skip）
- 编译/验收失败：AI 自愈（读错误日志→修 code/→重同步受影响文件→重跑该环节，上限 3 次）
  → 超限收据 fail（正文含失败现场：logcat/dmesg 摘录）
- adb 不可达 → 串口砖机三分法诊断（入口：用户目录 lc-skills-connection-serial
  或 minicom，DBG-006 关硬件流控）：
  * adb 不可达 + 串口静默（无任何输出）= 断电全砖 → 转人工
  * 串口有启动日志但 adb 起不来 = 半砖 → 收据 fail 附串口日志，交 emit 分析
  * 串口反复输出相同启动日志 = boot loop → 收据 fail 附循环片段，交 emit 分析
## Related policy IDs（关联规则 ID）
- SRC-001/002（修订后）、BLD-001~012、INC-001~010
---
## 工作流（参考实现细节）
1. 同步：python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --auto
   （同步源 = code 工作树当前状态；范围 = code/rpi5/{aosp,kernel}；
   data/verify、others/、rpi-zero2w 不参与同步）
2. 影响面判定：git status --porcelain + git diff --name-only → 分类
   （aosp 模块 / 内核 / boot 相关 / others 不同步）
3. 编译：lcview 相关先 make lechao_lcview_unit_test lechao_lcview_hal_test -j$(nproc)；
   增量路径按 incremental-dev-reference：
   - aosp 模块：m <module>（BLD-004 先 source build/envsetup.sh + lunch；BLD-005 禁裸 make）
   - boot/内核：make Image dtbs（BLD-001~003 Clang+LLD/产物拷贝 rpi5-kernel/；
     INC-006 Image+dtbs+overlays 同源；INC-009 android_rpi5_defconfig；INC-007 VINTF）
   - 打包：mk_rpi5_full_image.sh -mode 2|3|4（BLD-007 sudo 打包显式传
     TARGET_PRODUCT+ANDROID_PRODUCT_OUT；BLD-008 选对 mode）
   - 全程：INC-001 禁 make clean/clobber；BLD-009 CCACHE_DIR=out/ccache
4. adb 推送：python3 harness/skills/workspace-verify/ws_adb_connect.py ensure
   （mDNS→静态 fallback；成功输出 endpoint）
   adb root && adb remount（INC-003 失败查 verifiedbootstate=orange；INC-005 需 userdebug）
   → push 编译产物到对应分区路径（/system/... /vendor/...）→ 重启服务或 reboot
   （boot.img 刷写只写第一分区 INC-004，且属人工确认门）
5. 验收：python3 harness/skills/workspace-verify/ws_acceptance.py run --acceptance "<验收文本>"
   （语法标签自动执行；overall=ai 的自由文本项由 AI 用 logcat/dmesg 现场判定并覆盖）
6. 收据：python3 harness/skills/workspace-verify/ws_report.py ... 
   --body <正文文件>（**必传**：CDP 原文 + 各阶段明细 + 失败现场摘录，自动脱敏）
   模式 A 加 --batch-file <cdp> --target $(git rev-parse --short=12 HEAD)
## 退出码
- 0 验证完成（含 fail 收据落盘）；1 环境/参数错误；3 前置缺失
```

- [ ] **Step 2: 提交**

```bash
git add harness/skills/workspace-verify/SKILL.md
git commit -m "新增(workspace-verify): SKILL.md（模式A/B+boot默认/三分法判定条件/BLD-001~012 全规则）"
```

---

## 阶段 4：git-works-push skill

### Task 4.1: push 脚本（自包含精简版，含 --dry-run）

**Files:**
- Create: `harness/skills/git-works-push/git_works_push.sh`

- [ ] **Step 1: 实现脚本**

```bash
#!/usr/bin/env bash
# git-works-push（项目定制精简版）：collect diff → commit → push origin dev。
# 保留：永不推 main 守卫、--push-only、--dry-run、push 失败 commit 保留(exit 2)。
# 去掉：dev 自动创建、amend、message 三重校验。
set -u
BRANCH="${GIT_WORKS_BRANCH:-dev}"
MODE="normal"   # normal | push-only | dry-run
MSG_FILE=""

usage() { echo "usage: $0 [--push-only] [--dry-run] [--message-file <f>]"; exit 3; }

while [ $# -gt 0 ]; do
  case "$1" in
    --push-only) MODE="push-only" ;;
    --dry-run) MODE="dry-run" ;;
    --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

# 永不推 main 守卫
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  echo "error: 禁止推送到 $BRANCH" >&2; exit 1
fi
CUR=$(git branch --show-current)
if [ "$CUR" = "main" ] || [ "$CUR" = "master" ]; then
  echo "error: 当前分支 $CUR 禁止提交" >&2; exit 1
fi

if [ "$MODE" = "dry-run" ]; then
  echo "== dry-run：改动预览（不执行 add/commit/push）=="
  git status --porcelain
  git diff HEAD --stat | tail -5
  exit 0
fi

if [ "$MODE" = "normal" ]; then
  [ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { echo "error: 需 --message-file 且文件存在" >&2; exit 3; }
  [ -n "$(git status --porcelain)" ] || { echo "working tree clean" >&2; exit 4; }
  git add -A || { echo "error: git add 失败" >&2; exit 1; }
  git commit -F "$MSG_FILE" || { echo "error: commit 失败" >&2; exit 1; }
fi

if ! git push -u origin "$BRANCH"; then
  echo "error: push 失败（commit 已保留），请人工处理（如 pull --rebase 后 --push-only）" >&2
  exit 2
fi
echo "pushed: $BRANCH $(git rev-parse --short HEAD)"
exit 0
```

- [ ] **Step 2: 语法检查与干跑验证**

```bash
bash -n harness/skills/git-works-push/git_works_push.sh && chmod +x harness/skills/git-works-push/git_works_push.sh
bash harness/skills/git-works-push/git_works_push.sh --dry-run; echo "exit=$?"
```
Expected: 语法 OK；dry-run 输出当前改动预览（当前工作区有未提交改动时应列出）exit 0

- [ ] **Step 3: 提交**

```bash
git add harness/skills/git-works-push/
git commit -m "新增(git-works-push): 自包含精简 push 脚本（main 守卫/push-only/dry-run/失败保留）"
```

### Task 4.2: git-works-push SKILL.md + commit message 格式

**Files:**
- Create: `harness/skills/git-works-push/SKILL.md`
- Create: `harness/skills/git-works-push/docs/commit-message-format.md`

- [ ] **Step 1: 写 commit message 格式文档**

```markdown
# Commit Message 格式（git-works-push）

`<中文type>(<scope>): <subject>` + body bullet（可无）

type 词表（仅此六种）：新增 / 修复 / 重构 / 文档 / 构建 / 杂项
scope：改动行数最多的顶层目录或模块（如 cross-device / workspace-verify / baseline / dev）

示例：
新增(cross-device): CDP 契约解析器与契约文档
修复(workspace-verify): adb 连接静态 fallback 端口解析
构建(baseline): BL-20260823-01 晋升 promoted
```

- [ ] **Step 2: 写 SKILL.md**

```markdown
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

核心语义：收集工作树 diff → AI 生成中文 commit message → commit 并 push origin dev
（verify 收据随批入库）；验证与推送解耦，不做门禁，仅保留 git 流转守卫。
## Trigger（触发条件）
- cross-device-apply 编辑完成后（verify 收据已落盘）
- 人工单独提交 dev 改动
## Preconditions（前置条件）
- 当前分支 dev；工作树有改动（normal 模式）；收据文件 data/verify/ 已就位（随批入库）
## Human confirmation gates（人工确认门）
- 零确认
## Outputs / artifacts（输出/产物）
- origin/dev 新 commit（代码 + 收据同批）
- harness/log/git-works-push/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- push 失败（exit 2）：commit 保留，转人工处理（pull --rebase 后 --push-only）
- 无改动（exit 4）：提示无需推送
---
## 工作流
1. 收集 diff：git status --porcelain + git diff HEAD --stat
   （大 diff 降级：>50 文件或 >5000 行时每文件只取前 20 行，仅用于生成 message）
2. AI 生成中文 commit message（docs/commit-message-format.md，六种 type）
3. 预览确认链路（可选）：bash harness/skills/git-works-push/git_works_push.sh --dry-run
4. 执行：bash harness/skills/git-works-push/git_works_push.sh --message-file <临时文件>
5. 核对：git ls-remote origin dev == 本地 HEAD（不等于则报错转人工）
## 退出码
0 成功 / 1 守卫失败 / 2 push 失败（commit 保留）/ 3 参数错误 / 4 无改动
```

- [ ] **Step 3: 提交**

```bash
git add harness/skills/git-works-push/
git commit -m "新增(git-works-push): SKILL.md 与 commit message 格式（六种 type/dry-run/设备限定）"
```

---

## 阶段 5：cross-device pack（emit + apply）

### Task 5.1: emit precheck cdp_emit_precheck.py

**Files:**
- Create: `harness/skills/cross-device/lib/python/cdp_emit_precheck.py`

- [ ] **Step 1: 实现**

```python
"""emit 侧 precheck：pull / 工作树干净 / HEAD==origin/dev / 上批已推送。

判定「上批已推送」（spec §5.2）：读最新详情 verified_commit →
merge-base --is-ancestor(verified_commit, origin/dev) 且
origin/dev HEAD（short=12） != verified_commit。
无收据（首轮）视为通过。--no-pull 用于干跑（不执行网络操作）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_paths import project_root  # noqa: E402
from cdp_receipt import read_latest_receipt, read_trend_last  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=120)


def precheck(root=None, do_pull=True):
    root = Path(root) if root else project_root()
    try:
        if do_pull:
            r = _git(root, "pull")
            if r.returncode != 0:
                return False, "git pull 失败", r.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        return False, "git pull 超时", ""
    if _git(root, "status", "--porcelain").stdout.strip():
        return False, "工作树不干净", ""
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    origin = _git(root, "rev-parse", "origin/dev").stdout.strip()
    if head != origin:
        return False, "本地 HEAD != origin/dev", ""
    # 上批已推送判定（sha 统一 short=12 比较，防 40 位 vs 12 位恒不等）
    latest = read_latest_receipt(root / "data" / "verify")
    trend = read_trend_last(root / "data" / "verify")
    if latest and trend and latest.verified_commit:
        r = _git(root, "merge-base", "--is-ancestor",
                 latest.verified_commit, "origin/dev")
        origin_head12 = _git(root, "rev-parse", "--short=12",
                             "origin/dev").stdout.strip()
        if r.returncode != 0 or origin_head12 == latest.verified_commit:
            return False, f"上批({latest.batch_id})未推送", ""
    return True, "", ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="emit precheck")
    ap.add_argument("--no-pull", action="store_true", help="干跑：不执行 git pull")
    args = ap.parse_args(argv)
    ok, reason, detail = precheck(do_pull=not args.no_pull)
    print(json.dumps({"ok": ok, "reason": reason, "detail": detail[:100]},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 冒烟（干跑 + 真跑）**

```bash
python3 harness/skills/cross-device/lib/python/cdp_emit_precheck.py --no-pull
python3 harness/skills/cross-device/lib/python/cdp_emit_precheck.py
```
Expected: 输出 JSON；当前工作树若有未提交改动则 ok:false reason:工作树不干净（属正确行为）

- [ ] **Step 3: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): emit precheck（short=12 比较/JSON 输出/--no-pull 干跑）"
```

### Task 5.2: cross-device-emit SKILL.md

**Files:**
- Create: `harness/skills/cross-device/cross-device-emit/SKILL.md`

- [ ] **Step 1: 编写 SKILL.md**

```markdown
---
name: cross-device-emit
description: emit 侧（远端）分析仓内上下文、生成 CDP 批次纯文本（-s/-sv），selfcheck 后交用户拷贝到 apply 设备。
no_commit: true
stages:
  - research: "precheck + 上下文组装"
  - plan: "强 LLM 分析并拆多轮"
  - code: "生成 CDP 文本 + selfcheck"
  - review: "输出批次"
---
# cross-device-emit

> **仅限 emit 设备（远端）运行**；本 skill 在 apply 设备也可见（repo 即 pack），
> 但 apply 设备不得触发它产出批次。

核心语义：远端强 LLM 基于仓内上下文（main..dev diff、涉及文件全量、最新 verify
收据、docs）产出 CDP 批次纯文本（-s/-sv），selfcheck 后交用户拷贝到 apply 设备。
## Trigger（触发条件）
- 用户在本仓 clone 上 git pull 后，准备发起新一轮跨设备修改
## Preconditions（前置条件）
- 工作树干净；本地 HEAD == origin/dev；上批收据已推送（cdp_emit_precheck.py）
## Human confirmation gates（人工确认门）
- 零确认（产出批次文本，不落盘不提交）
## Outputs / artifacts（输出/产物）
- 纯文本 CDP 批次（stdout，用户拷贝）；临时文件 harness/log/cross-device-emit/（gitignore）
## Failure / recovery（失败/恢复）
- precheck 不过：按 reason 处理（pull 失败网络/树脏/上批未推拒产）
- selfcheck 不过：AI 修批次后重跑
## Related policy IDs（关联规则 ID）
- CDP-001（契约成对修改）
---
## 工作流
1. precheck：python3 harness/skills/cross-device/lib/python/cdp_emit_precheck.py
2. 上下文组装（指引强 LLM）：
   - git diff main..dev（全量；聚焦时可看上批 batch_base..dev）
   - 涉及文件在 code/ 下的全量内容（modified 看 .diff、new 看全量）
   - 最新 data/verify 收据（失败现场摘录）
   - 相关 docs/ 章节
3. 产批：-s/-sv + base + 意图/验收/方向，总字符 50~500；
   base 自动取 precheck 后 origin/dev HEAD 前 12 位
   （git rev-parse --short=12 origin/dev，勿手算）；复杂任务拆多轮，每轮注明后续轮次
4. selfcheck：python3 harness/skills/cross-device/lib/python/cdp_parse.py
   --role emit <批次临时文件>（必须 exit 0）
5. 输出：纯文本批次，无包裹标记；产一批等一批，不并行产下一条
## 约束（禁止）
- emit 侧禁止 git commit/push、禁止修改 code/（流程纪律，无技术强制，违者评审回退）
```

- [ ] **Step 2: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): cross-device-emit SKILL.md（设备限定/base 自动获取/main..dev 全量上下文）"
```

### Task 5.3: cross-device-apply SKILL.md

**Files:**
- Create: `harness/skills/cross-device/cross-device-apply/SKILL.md`

- [ ] **Step 1: 编写 SKILL.md**

```markdown
---
name: cross-device-apply
description: 解析 CDP 批次 → 编辑 code/（dev）→ 拉起 workspace-verify（-sv）→ 拉起 git-works-push。
no_commit: false
stages:
  - research: "precheck + 批次解析"
  - plan: "AI 制定编辑计划"
  - code: "编辑 + manifest 重生成 + verify + push"
  - review: "核对推送结果"
---
# cross-device-apply

> **仅限 apply 设备（本地 WSL2）运行**（需 workspace 与开发板访问）。

核心语义：解析 CDP 批次（base 拒批门），按编辑载体规则改 code/（new 全量直改、
modified/*.diff hunk 内编辑+校验器），-sv 拉起 workspace-verify，统一经 git-works-push
推送（失败收据随批入库供 emit 分析）。
## Trigger（触发条件）
- 用户粘贴 emit 侧 CDP 批次文本
## Preconditions（前置条件）
- 当前分支 dev；工作树干净；批次 base == 本地 HEAD 前 12 位
  （--expect-base 比对，不匹配 exit 3 拒批，回 emit 重产）
## Human confirmation gates（人工确认门）
- 零确认；高危动作（整卡刷写/boot dd）由 workspace-verify 内部确认
## Outputs / artifacts（输出/产物）
- code/ 编辑结果 + 重生成 manifest.yaml + data/verify 收据（随批 commit 推送）
- harness/log/cross-device-apply/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- 编辑失败：AI 自愈（上限 3 次）；超限标 FAILED 继续，收据 fail
- diff 编辑后跑 cdp_validate_patch.py；verify 同步 git apply --check 失败走自愈
- verify 失败仍 push（失败收据供 emit 分析）；push 失败转人工
## Related policy IDs（关联规则 ID）
- CDP-001、SRC-001/002（修订后）
---
## 工作流
1. 接收批次：用户粘贴 → AI 存临时文件 harness/log/cross-device-apply/batch-<ts>.cdp
2. precheck（含 base 拒批）：
   python3 harness/skills/cross-device/lib/python/cdp_parse.py --role apply
     --expect-base "$(git rev-parse --short=12 HEAD)" <批次文件>
   （exit 0 通过；17 在 apply 角色降级 WARN，16 预算超限仍 blocking；
    exit 3 = base 不匹配或参数错误，整批拒绝回 emit）
3. 编辑：按批次意图/方向编辑 code/ 全目录：
   - new/、rpi-zero2w/、others/：全量文件直接编辑
   - modified/*.diff：hunk 内编辑（+ 行/已有 context），禁引入新 context；
     每个编辑过的 .diff 跑：
     python3 harness/skills/cross-device/lib/python/cdp_validate_patch.py <diff 文件>
   - 涉及 code/rpi5 时：python3 harness/skills/sync-workspace-to-code/
     sync_workspace_to_code.py --gen-manifest-only
4. 分流：
   - -sv → 拉起 @workspace-verify（模式 A，--batch-file <批次文件>）；
     收据正文必须含 CDP 原文 + 失败现场（--body）
   - -s → 写 skip 收据：
     python3 harness/skills/workspace-verify/ws_report.py
       --batch-file <批次文件> --result skip --build skip --board skip
       --summary "<意图首句>（-s 无需上板）" --body <批次文件>
5. 拉起 @git-works-push（收据+代码统一 commit push）
## 退出码
- 0 完成（含 fail 收据已推送）；1 自愈失败；3 precheck 拒批（base 不匹配回 emit）
```

- [ ] **Step 2: 提交**

```bash
git add harness/skills/cross-device/
git commit -m "新增(cross-device): cross-device-apply SKILL.md（--expect-base 拒批/编辑载体/skip 收据调用）"
```

---

## 阶段 6：sync-modify-to-main-base skill

### Task 6.1: baseline 登记辅助 baseline_register.py

**Files:**
- Create: `harness/skills/sync-modify-to-main-base/baseline_register.py`

- [ ] **Step 1: 实现**

```python
"""baseline-status.yaml candidate/promoted 登记辅助。

新流程登记从 candidate 起步（archive 仅旧流程历史）。
sync_manifest 字段复用为 data/verify 收据路径（spec §7）。
save() 手工保留 yaml 头部注释块（PyYAML 往返不保留注释）。
"""
import argparse
import datetime
import sys
from pathlib import Path

import yaml

# 本文件位于 harness/skills/sync-modify-to-main-base/，parents[2] = harness
CONFIG = Path(__file__).resolve().parents[2] / "config" / "baseline-status.yaml"


def load():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def save(data):
    """整文件重写但保留头部 '#' 注释行（语义说明不丢失）。"""
    text = CONFIG.read_text(encoding="utf-8")
    header = "".join(ln for ln in text.splitlines(keepends=True)
                     if ln.startswith("#"))
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    CONFIG.write_text(header + body, encoding="utf-8")


def next_id(data, today):
    existing = [b.get("baseline_id") for b in data.get("baselines", [])]
    n = 1
    while f"BL-{today}-{n:02d}" in existing:
        n += 1
    return f"BL-{today}-{n:02d}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="baseline candidate/promoted 登记")
    ap.add_argument("action", choices=["add-candidate", "promote"])
    ap.add_argument("--baseline-id")
    ap.add_argument("--source-commit")
    ap.add_argument("--receipt-path")
    ap.add_argument("--approved-by")
    args = ap.parse_args(argv)

    data = load()
    baselines = data.setdefault("baselines", [])
    today = datetime.date.today().strftime("%Y%m%d")

    if args.action == "add-candidate":
        bid = args.baseline_id or next_id(data, today)
        baselines.append({
            "baseline_id": bid,
            "status": "candidate",
            "source_branch": "dev",
            "source_commit": args.source_commit,
            "sync_manifest": args.receipt_path,
            "build_result": "PASS",
            "package_result": "PASS",
            "board_verify": "PASS",
            "evidence": {
                "build_result": "PASS",
                "package_result": "PASS",
                "board_verify": "PASS",
                "sync_manifest": args.receipt_path,
            },
        })
        save(data)
        print(f"candidate: {bid}")
        return 0

    if args.action == "promote":
        for b in baselines:
            if b.get("baseline_id") == args.baseline_id:
                b["status"] = "promoted"
                b["approved_by"] = args.approved_by or "lechao"
                b["approved_at"] = datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                save(data)
                print(f"promoted: {args.baseline_id}")
                return 0
        print(f"error: 未找到 baseline {args.baseline_id}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 冒烟（--help 与真实 load）**

```bash
python3 harness/skills/sync-modify-to-main-base/baseline_register.py --help
python3 - <<'EOF'
import sys
sys.path.insert(0, "harness/skills/sync-modify-to-main-base")
import baseline_register as br
d = br.load()
assert isinstance(d.get("baselines"), list), "CONFIG 路径错误或 yaml 结构异常"
print("config ok:", br.CONFIG)
EOF
```
Expected: usage 显示；`config ok: .../harness/config/baseline-status.yaml`（验证 parents[2] 路径正确）

- [ ] **Step 3: 提交**

```bash
git add harness/skills/sync-modify-to-main-base/
git commit -m "新增(sync-modify-to-main-base): baseline 登记辅助（parents[2] 路径/头部注释保留）"
```

### Task 6.2: sync-modify-to-main-base 脚本与 SKILL.md

**Files:**
- Create: `harness/skills/sync-modify-to-main-base/sync_modify_to_main_base.sh`
- Create: `harness/skills/sync-modify-to-main-base/SKILL.md`

- [ ] **Step 1: 实现脚本（移植 branch-merge + baseline 闭环 + v2 修复）**

```bash
#!/usr/bin/env bash
# sync-modify-to-main-base：dev → main squash promote + 重建 dev + baseline 晋升。
# 前置：最新收据 result∈{pass,skip} 且 HEAD^(--short=12) == verified_commit。
# prepare：登记 candidate（随 dev 提交推送）；promote：晋升 promoted + squash + 重建。
set -u
MODE=""; MSG_FILE=""; BID=""

usage() { echo "usage: $0 --prepare | --promote --baseline-id <id> --message-file <f>"; exit 3; }
[ $# -ge 1 ] || usage
case "$1" in
  --prepare) MODE="prepare" ;;
  --promote)
    MODE="promote"; shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --baseline-id) [ $# -ge 2 ] || usage; BID="$2"; shift ;;
        --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
        *) usage ;;
      esac
      shift
    done ;;
  *) usage ;;
esac

# ── 前置校验（prepare/promote 共用；sha 统一 short=12 比较）─────────
LATEST=$(ls -1 data/verify/*.md 2>/dev/null | grep -v trend.md | sort | tail -1)
[ -n "$LATEST" ] || { echo "error: 无 verify 收据" >&2; exit 1; }
RESULT=$(sed -n 's/^- result: //p' "$LATEST" | head -1)
VC=$(sed -n 's/^- verified_commit: //p' "$LATEST" | head -1)
case "$RESULT" in
  pass|skip) ;;
  *) echo "error: 最新收据 result=$RESULT 非 pass/skip（revert/fail 收据不可 promote）" >&2; exit 1 ;;
esac
PARENT=$(git rev-parse --short=12 HEAD^ 2>/dev/null || echo "")
[ "$PARENT" = "$VC" ] || {
  echo "error: HEAD^($PARENT) != verified_commit($VC)：dev 存在未验证改动" >&2; exit 1; }

if [ "$MODE" = "prepare" ]; then
  git fetch origin || { echo "error: fetch 失败" >&2; exit 1; }
  CNT=$(git rev-list --count main..dev)
  [ "$CNT" -gt 0 ] || { echo "dev 无领先 main 的提交（exit 4）"; exit 4; }
  python3 harness/skills/sync-modify-to-main-base/baseline_register.py add-candidate \
    --source-commit "$(git rev-parse --short=12 HEAD)" --receipt-path "$LATEST" \
    || { echo "error: candidate 登记失败" >&2; exit 1; }
  # 登记随 dev 提交推送（避免弄脏工作树阻塞后续 precheck）
  git add harness/config/baseline-status.yaml
  git commit -m "构建(baseline): 登记 candidate（receipt=$(basename "$LATEST")）" || true
  git push origin dev || echo "warn: candidate 登记推送失败，请人工 push"
  echo "candidate 已登记并推送；人工评审后执行："
  echo "  $0 --promote --baseline-id <id> --message-file <f>"
  exit 0
fi

# ── promote ────────────────────────────────────────────────────────
[ -n "$BID" ] || { echo "error: --baseline-id 必填" >&2; exit 3; }
[ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { echo "error: --message-file 缺失或不存在" >&2; exit 3; }
python3 harness/skills/sync-modify-to-main-base/baseline_register.py promote \
  --baseline-id "$BID" \
  || { echo "error: baseline 晋升登记失败（检查 $BID 是否为 candidate）" >&2; exit 1; }
# 晋升登记随 dev 提交（squash 时一并进入 main；重建 dev 后仍在——reset --hard 前）
git add harness/config/baseline-status.yaml
git commit -m "构建(baseline): ${BID} 晋升 promoted" || true

git checkout main && git pull origin main || { git checkout dev; exit 1; }
git merge --squash dev || { git checkout dev; exit 1; }
git commit -F "$MSG_FILE" || { git checkout dev; exit 1; }
git diff --quiet main dev || { echo "error: squash 后 main 与 dev 内容不一致" >&2; exit 1; }
git push origin main || { echo "error: push main 失败（dev 未动）" >&2; exit 2; }

# 重建 dev（delete 失败则强推 +dev；再失败转人工）
git checkout dev && git reset --hard main || exit 1
if git push origin --delete dev 2>/dev/null; then
  git push -u origin dev || { echo "error: dev 重建推送失败" >&2; exit 2; }
else
  git push origin +dev || { echo "error: dev 强推失败，请人工处理" >&2; exit 2; }
fi

echo "promote 完成；AI 须立即执行 /sync-code-to-doc 工作流同步设计文档"
exit 0
```

（关键时序说明：promote 登记提交在 `reset --hard` **之前**完成于 dev，
squash 会把它带入 main，重建后 dev/main 均保留记录，工作树保持干净。）

- [ ] **Step 2: 写 SKILL.md**

```markdown
---
name: sync-modify-to-main-base
description: dev 验证 OK 后 promote 到 main 生成基线（candidate 自动登记 → 人工评审 → promoted）。
no_commit: true
stages:
  - research: "前置校验（收据 pass/skip + HEAD^ 判定）"
  - plan: "prepare 登记 candidate"
  - code: "promote squash + 重建 dev"
  - review: "拉起文档同步"
---
# sync-modify-to-main-base

> **仅限 apply 设备（本地 WSL2）运行**。

核心语义：dev 最新收据 pass/skip 且无未验证改动时，prepare 登记 candidate →
人工评审 → promote（squash 到 main + 重建 dev + 晋升 promoted）→ AI 拉起文档同步。
## Trigger（触发条件）
- emit 侧宣告任务结束，dev 最新收据 pass/skip，准备生成新基线
## Preconditions（前置条件）
- 最新收据 result∈{pass,skip}（-s 批次 skip 视为 OK）且 HEAD^ == verified_commit
  （dev 无未验证改动；revert/fail 收据拒绝）；dev 领先 main ≥1 提交
## Human confirmation gates（人工确认门）
- prepare 与 promote 之间的人工评审（检查 candidate 记录与 dev 内容）
## Outputs / artifacts（输出/产物）
- main 新 squash commit（含代码+收据+baseline 登记）；dev 重建指向 main
- harness/log/sync-modify-to-main-base/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- push main 失败（exit 2）：dev 未动，人工处理后重试
- dev 重建失败：delete 失败自动 +dev 强推；再失败 exit 2 转人工
## Related policy IDs（关联规则 ID）
- SRC-004（promoted 才是恢复真相源）
---
## 工作流
1. 前置校验 + prepare：
   bash harness/skills/sync-modify-to-main-base/sync_modify_to_main_base.sh --prepare
   （输出登记的 baseline_id；candidate 随 dev 提交推送）
2. 人工评审：检查 baseline-status.yaml candidate 记录（收据路径可点开核对）
3. AI 生成 squash message 后 promote：
   bash .../sync_modify_to_main_base.sh --promote --baseline-id <id> --message-file <f>
4. **promote 成功后 AI 必须立即执行 /sync-code-to-doc 工作流**（文档同步入闭环，
   脚本无法自动拉起 opencode 命令，由 AI 在会话内触发）
## 退出码
0 成功 / 1 校验失败 / 2 push 类失败 / 3 参数错误 / 4 dev 无领先提交
```

- [ ] **Step 3: 语法检查**

```bash
bash -n harness/skills/sync-modify-to-main-base/sync_modify_to_main_base.sh && echo OK
```
Expected: OK

- [ ] **Step 4: 提交**

```bash
git add harness/skills/sync-modify-to-main-base/
git commit -m "新增(sync-modify-to-main-base): promote 全链（short=12 比较/--baseline-id 必填/登记随 dev 提交/+dev 强推兜底）"
```

---

## 阶段 7：revert-modify-from-main-base skill

### Task 7.1: revert 脚本与 SKILL.md

**Files:**
- Create: `harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh`
- Create: `harness/skills/revert-modify-from-main-base/SKILL.md`

- [ ] **Step 1: 实现脚本**

```bash
#!/usr/bin/env bash
# revert-modify-from-main-base：dev 硬重置 origin/main + force push + revert 收据。
# 仅人工触发；无 --execute 时仅预览丢弃清单（确认门），不做任何变更。
set -u
if [ "${1:-}" != "--execute" ]; then
  echo "== 将丢弃 origin/main..dev 的提交 =="
  git log origin/main..dev --oneline
  echo "确认后执行: $0 --execute"
  exit 0
fi
git fetch origin || exit 1
OLD=$(git rev-parse dev)
CNT=$(git rev-list --count origin/main.."$OLD")
git checkout dev || exit 1
git reset --hard origin/main || exit 1
git push --force origin dev || { echo "error: force push 失败" >&2; exit 2; }

# revert 收据（result: revert 专用枚举；11 字段补齐；batch_id 8 位时间戳）
BID="revert-$(date +%y%m%d%H%M)"
RCPT="data/verify/$(date +%Y%m%d-%H%M%S)-${BID}.md"
{
  echo "- schema_version: 1"
  echo "- batch_id: ${BID}"
  echo "- batch_base: $(git rev-parse --short=12 origin/main)"
  echo "- verified_commit: $(git rev-parse --short=12 origin/main)"
  echo "- verify_mode: board"
  echo "- result: revert"
  echo "- build: skip"
  echo "- push_board: skip"
  echo "- acceptance: 无（revert 操作收据）"
  echo "- elapsed_s: 0"
  echo "- summary: 回退 dev 到 main（丢弃 ${CNT} 个提交，起点 ${OLD:0:12}）"
  echo
  echo "## body"
  echo
  echo "## 被丢弃提交"
  git log "$OLD" --not origin/main --oneline
} > "$RCPT"
# 收据随 dev 提交推送（避免弄脏工作树）
git add "$RCPT"
git commit -m "杂项(dev): 回退 dev 至 main 基线（丢弃 ${CNT} 提交）" || true
git push origin dev || echo "warn: revert 收据推送失败，请人工 push"

echo "revert 收据: $RCPT"
echo "AI 须立即执行恢复验证：/workspace-verify（模式 B：--target main --prefix revert，默认含 boot 验收）"
exit 0
```

- [ ] **Step 2: 写 SKILL.md**

```markdown
---
name: revert-modify-from-main-base
description: dev 持续 NG 且 emit 侧强模型无法修复时，人工回退 dev 到 main 基线并恢复开发板。
no_commit: true
stages:
  - research: "丢弃清单预览（确认门）"
  - plan: "reset + force push + revert 收据"
  - code: "code→workspace 同步回 main"
  - review: "恢复验证（模式 B）"
---
# revert-modify-from-main-base

> **仅限 apply 设备（本地 WSL2）运行**；永不自动触发，仅在 emit 侧强模型
> 多轮修复失败后由用户手动执行。

核心语义：人工确认丢弃清单后，dev 硬重置 origin/main + force push，写 revert 收据，
code→workspace 同步回 main 并跑一次恢复验证确保开发板可启动。
## Trigger（触发条件）
- 用户显式触发（dev 持续 NG 且正向修复无望）
## Preconditions（前置条件）
- origin/main 可达；dev 与 main 的分叉已确认无抢救价值
## Human confirmation gates（人工确认门）
- 预览模式列出 origin/main..dev 丢弃清单，用户显式确认后 --execute
## Outputs / artifacts（输出/产物）
- dev 重置到 origin/main（force push）；revert 收据（result: revert，含被丢弃提交清单）
- harness/log/revert-modify-from-main-base/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- force push 失败（exit 2）转人工；恢复验证失败说明 main 基线本身异常，
  人工介入（不得再次自动 revert）
## Related policy IDs（关联规则 ID）
- SRC-004（promoted/main 为恢复真相源）
---
## 工作流
1. 预览：bash harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh
   （列丢弃清单，不改任何状态）
2. 确认后执行：... --execute（reset --hard origin/main + force push + revert 收据随 dev 提交）
3. code→workspace 同步回 main 状态：
   python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --auto
4. 恢复验证：拉起 @workspace-verify 模式 B（--target main --prefix revert；
   默认含 boot 验收），确保开发板恢复正常基线
## 退出码
0 成功 / 1 git 操作失败 / 2 force push 失败 / 3 参数错误
```

- [ ] **Step 3: 语法检查 + 预览冒烟（不执行）**

```bash
bash -n harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh && echo OK
bash harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh
```
Expected: 语法 OK；预览输出丢弃清单（当前 dev 领先 main 的提交）exit 0

- [ ] **Step 4: 提交**

```bash
git add harness/skills/revert-modify-from-main-base/
git commit -m "新增(revert-modify-from-main-base): 人工回退（预览确认门/rev-list 计数/收据全字段+随批提交）"
```

---

## 阶段 8：command 薄入口 + 规则与文档修订

### Task 8.1: 6 个 command 薄入口（统一纯 @ 引用形式）

**Files:**
- Create: `.opencode/command/cross-device-emit.md`
- Create: `.opencode/command/cross-device-apply.md`
- Create: `.opencode/command/workspace-verify.md`
- Create: `.opencode/command/git-works-push.md`
- Create: `.opencode/command/sync-modify-to-main-base.md`
- Create: `.opencode/command/revert-modify-from-main-base.md`
- Modify: `.opencode/command/sync-workspace-to-code.md`（标注 deprecated）

- [ ] **Step 1: 创建 6 个 command 文件**（统一纯 @ 引用形式——这些是 AI 工作流型
  skill，由 AI 按 SKILL.md 驱动脚本，command 不直接执行脚本）

`.opencode/command/cross-device-emit.md`：
```markdown
---
description: emit 侧生成 CDP 批次（分析仓内上下文 → 产批 → selfcheck → 输出纯文本，仅 emit 设备）
---
严格遵循完整工作流（precheck → 上下文组装 → 产批 → selfcheck → 输出）：
@harness/skills/cross-device/cross-device-emit/SKILL.md
```

`.opencode/command/cross-device-apply.md`：
```markdown
---
description: 解析 CDP 批次并编辑 code/dev（-sv 拉起验证，完成后推送 dev，仅 apply 设备）
---
严格遵循完整工作流（precheck 含 base 拒批 → 编辑载体规则 → verify 分流 → push）：
@harness/skills/cross-device/cross-device-apply/SKILL.md
```

`.opencode/command/workspace-verify.md`：
```markdown
---
description: code→workspace 同步、增量编译、adb 推送、验收并写 data/verify 收据（仅 apply 设备）
---
严格遵循完整工作流（模式 A 批次 / 模式 B 独立触发，默认 boot 验收）：
@harness/skills/workspace-verify/SKILL.md
```

`.opencode/command/git-works-push.md`：
```markdown
---
description: 收集 diff → 中文 commit message → commit + push origin dev（仅 apply 设备）
---
严格遵循完整工作流（diff 收集 → message 生成 → dry-run 预览可选 → 推送 → 核对）：
@harness/skills/git-works-push/SKILL.md
```

`.opencode/command/sync-modify-to-main-base.md`：
```markdown
---
description: dev 验证 OK 后 promote 到 main 生成基线（candidate → 人工评审 → promoted，仅 apply 设备）
---
严格遵循完整工作流（前置校验 → prepare 登记 → 人工评审 → promote → AI 立即拉起 /sync-code-to-doc）：
@harness/skills/sync-modify-to-main-base/SKILL.md
```

`.opencode/command/revert-modify-from-main-base.md`：
```markdown
---
description: dev 持续 NG 时人工回退 dev 到 main 基线并恢复开发板（仅 apply 设备）
---
严格遵循完整工作流（丢弃清单确认 → reset → force push → revert 收据 → 同步 → 恢复验证）：
@harness/skills/revert-modify-from-main-base/SKILL.md
```

- [ ] **Step 2: 标注 sync-workspace-to-code deprecated**

`.opencode/command/sync-workspace-to-code.md` 的 description 改为：

```markdown
---
description: "[DEPRECATED] 旧流程归档命令，新流程 code/dev 为源头，仅历史场景保留"
---
```

（正文保留原样；不删除文件。）

同步在 `harness/skills/sync-workspace-to-code/SKILL.md` frontmatter 之后标题下加：

```markdown
> **DEPRECATED**：流程反转后 workspace→code 归档方向消亡，仅历史场景保留；
> 新流程见 cross-device-apply / workspace-verify。
```

- [ ] **Step 3: 提交**

```bash
git add .opencode/command/ harness/skills/sync-workspace-to-code/SKILL.md
git commit -m "新增(opencode): 6 个 cross-device 命令薄入口（纯 @ 引用）+ deprecated 标注"
```

### Task 8.2: SRC 规则文件全面改写（source-code-modify.md）

**Files:**
- Modify: `harness/rules/source-code-modify.md`

- [ ] **Step 1: 改写 SRC 条目（保持现有 blockquote `> -` 格式）**

将头部 SRC 条目替换为（注意保留 `>` 前缀，与现有文档结构一致）：

```markdown
> - **SRC-001（修订）**：`code/`（dev 分支）是唯一改动源头；`~/workspace/` 是编译缓存。
>   所有定制改动必须先改 code/（经 cross-device-apply 或手工编辑），再经
>   workspace-verify 同步到 workspace 编译验证。手工调试允许临时改 workspace 试验，
>   但必须回填到 code/ 才能进入验证/推送链路（否则验证结果不代表 code 状态）。
> - **SRC-002（修订）**：workspace 是 code 的编译缓存镜像，由 workspace-verify /
>   sync-code-to-workspace 单向同步（code → workspace）；禁止把 workspace 改动
>   反向归档回 code（sync-workspace-to-code 已 deprecated，方向消亡）。
> - **SRC-003**：`code/others/` 不依赖 workspace，允许独立维护。
> - **SRC-004（修订）**：仅 promoted（main 分支基线）可作为恢复真相源；dev 迭代
>   状态以 data/verify 收据为准，未验证的 dev 改动不得宣称为基线；证据字段须按
>   baseline-evidence-template.yaml 填写并在 baseline-status.yaml 登记。
```

- [ ] **Step 2: 同步改正文冲突章节（仅改条目会自相矛盾）**

- **改动规则表**（约 L17）：将「必须先改 `~/workspace/` 源码……通过
  sync-workspace-to-code 同步归档」改为「必须先改 `code/`（dev 分支）→ 经
  workspace-verify 同步 workspace 编译验证 → 收据随批 push dev → 验证 OK 后
  sync-modify-to-main-base 晋升 main」。
- **禁止行为节**（约 L29-34）：将「`code/` 下的目录……严禁手动修改」改为
  「`code/` 允许经 cross-device-apply 或人工编辑（新流程源头）；严禁把 workspace
  改动反向归档回 code；sync-workspace-to-code 为 deprecated 历史命令」。
- **归档纪律表**（约 L40）：「未打包/未上板禁止执行 sync」改为「未上板验证的
  dev 改动禁止 promote 到 main；调试脚本 bug 时用 --check-only/--dry-run 验证」。
- 全文搜索 `sync-workspace-to-code`，逐处确认措辞与新流程一致（不删除历史说明，
  但标注 deprecated）。

- [ ] **Step 3: 提交**

```bash
git add harness/rules/source-code-modify.md
git commit -m "规则(harness): source-code-modify 全面改写（源头反转+正文规则表/禁止行为/归档纪律同步）"
```

### Task 8.3: AGENTS.md / harness/README.md / baseline 模板同步

**Files:**
- Modify: `AGENTS.md`
- Modify: `harness/README.md`
- Modify: `harness/config/baseline-evidence-template.yaml`

- [ ] **Step 1: AGENTS.md 命令表更新**

在「Harness 工作流命令」表追加 6 行（沿用现有表格式）：

```markdown
| `/cross-device-emit` | emit 侧生成 CDP 批次（远端强 LLM 分析后产批，输出纯文本，仅 emit 设备） |
| `/cross-device-apply` | 解析 CDP 批次编辑 code/dev，-sv 拉起验证后推送（仅 apply 设备） |
| `/workspace-verify` | code→workspace 同步、增量编译、上板验证并写 data/verify 收据（仅 apply 设备） |
| `/git-works-push` | dev 分支 commit + push（收据随批入库，仅 apply 设备） |
| `/sync-modify-to-main-base` | dev 验证 OK 后 promote 到 main 生成基线（三段式证据链，仅 apply 设备） |
| `/revert-modify-from-main-base` | dev 持续 NG 人工回退到 main 基线并恢复设备（仅 apply 设备） |
```

并在 sync-workspace-to-code 行追加 `（DEPRECATED）`；「源码改动优先级」章节措辞
改为「code/dev 是唯一改动源头，workspace 是编译缓存」；「Baseline 使用指引」更新
为 candidate 由 verify 收据经 --prepare 自动登记、人工评审后 promote。

- [ ] **Step 2: harness/README.md 更新**

- 「快速使用」首句「三个工作流命令」数字同步（3 个历史命令 + 6 个新命令，并标注
  sync-workspace-to-code deprecated）。
- 目录结构补 cross-device / workspace-verify / git-works-push /
  sync-modify-to-main-base / revert-modify-from-main-base。
- 控制总纲补「新流程 candidate 由 verify 收据自动登记（--prepare）」。

- [ ] **Step 3: baseline-evidence-template.yaml 注释更新**

`sync_manifest` 字段注释改为：

```yaml
# sync_manifest: 旧流程为同步计划产物路径；新流程（cross-device）为 data/verify 收据路径
```

同时该文件头部晋升规则区补一行：`# 新流程：candidate 由 sync-modify-to-main-base --prepare 依据最新 verify 收据自动登记，archive 阶段仅旧流程历史`

- [ ] **Step 4: 提交**

```bash
git add AGENTS.md harness/README.md harness/config/baseline-evidence-template.yaml
git commit -m "文档(repo): 命令表/SRC 措辞/baseline 模板同步（cross-device 工作流全面切换）"
```

---

## 阶段 9：端到端验收

### Task 9.1: 全链冒烟（-s 批次，不依赖上板）

**Files:**
- 无新文件（链路验证）

- [ ] **Step 1: 准备 -s 批次（真实小改动，走 apply→收据→push 全链）**

```bash
BASE=$(git rev-parse --short=12 HEAD)
cat > /tmp/opencode/e2e.cdp <<EOF
-s base:${BASE}
意图: usb-verify README 补充构建说明
验收: 无
方向: 在 code/rpi5/others/usb-verify/README.md 末尾追加构建小节
EOF
python3 harness/skills/cross-device/lib/python/cdp_parse.py --role apply \
  --expect-base "$(git rev-parse --short=12 HEAD)" /tmp/opencode/e2e.cdp
echo "exit=$?"
```
Expected: exit 0（若当前 dev 工作树不干净需先处理；others/ 改动不需要 manifest
重生成与同步——正好适配 -s 链路验证）

- [ ] **Step 2: apply 链路（AI 执行）**

1. 按 batch 意图编辑 `code/rpi5/others/usb-verify/README.md`（全量文件直接编辑，
   追加「## 构建」小节，内容自拟 3-5 行）。
2. 写 skip 收据：
   ```bash
   python3 harness/skills/workspace-verify/ws_report.py \
     --batch-file /tmp/opencode/e2e.cdp --result skip --build skip --board skip \
     --summary 'usb-verify README 补充构建说明（-s 无需上板）' \
     --body /tmp/opencode/e2e.cdp
   ```
3. push：
   ```bash
   printf '杂项(others): usb-verify README 补充构建说明\n' > /tmp/opencode/msg.txt
   bash harness/skills/git-works-push/git_works_push.sh --message-file /tmp/opencode/msg.txt
   ```

- [ ] **Step 3: 链路数据核验**

```bash
git ls-remote origin dev | cut -c1-12   # 应 == 本地 git rev-parse --short=12 HEAD
ls data/verify/ && tail -1 data/verify/trend.md
git show --stat HEAD | head -10          # 收据+README 应同批入库
```
Expected: 远端==本地；trend 末行 batch_id 为该批 sha 前 12 位、result=skip；commit
同时含 README 与收据文件

- [ ] **Step 4: revert 预览演示（不执行）+ promote 前置负例**

```bash
bash harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh
bash harness/skills/sync-modify-to-main-base/sync_modify_to_main_base.sh --prepare; echo "exit=$?"
```
Expected: revert 预览列出丢弃清单 exit 0（未做任何变更）；promote prepare 正常时应
登记 candidate（真实场景）——若不想在冒烟中登记 candidate，跳过此命令并在报告中
注明「promote/revert 真实链路由用户在首次实战中验证」

- [ ] **Step 5: 收尾说明**

向用户报告：-s 全链可用（parse→编辑→收据→push→远端核对）；`-sv` 上板链路、
promote、revert 的真实执行需在有开发板/真实任务场景由用户首跑验证。

---

## 自审记录（v2，完成后核对）

- **Spec 覆盖**：契约（T1.1，含 --expect-base 拒批）✓ 验收（T3.2）✓ 收据（T0.2/T3.3，
  含 CDP 原文 --body 必传）✓ emit（T5.1/5.2，short=12 判定）✓ apply（T5.3）✓
  verify（T3.1-3.4，三分法判定条件+模式 B boot 默认）✓ push（T4，dry-run+六 type）✓
  promote（T6，short=12+--baseline-id+登记随批提交）✓ revert（T7，全字段收据+前缀）✓
  command+规则（T8，blockquote 格式+正文全面改写）✓ 端到端（T9，-s 全链）✓
- **检视修复落实**：Critical C1-C6（路径/正则/位数/语法/API/CLI 一致）✓
  High H1-H8（mdns/state 判定/标签语义/diff 元信息/15 可达/拒批门/CLI 入口/promote 提交）✓
  Medium M1-M9（登记时序/+dev 强推/--baseline-id/8 位时间戳/yaml 头注释/测试隔离/
  usage 修复/CLI exit 3）✓ spec 微修 4 处（已另行提交）✓
- **占位符检查**：无 TBD/TODO；两处「以 Step 1 核实为准」是对现有脚本签名的
  显式核对指令（核对目标与代码已给出），非占位符
- **类型一致性**：`Batch.mode`（"s"/"sv"）、`Receipt` 11 字段、`split_tag` 返回
  (kind, payload)、`run_acceptance` overall ∈ pass|fail|ai、`baseline_register`
  参数、`ws_report` --prefix 枚举全计划一致
- **注意**：Task 2.1 修改现有 925 行脚本，以工作区当前状态（含未提交
  `_resolve_proj_cwd` 修复）为基础，勿覆盖

