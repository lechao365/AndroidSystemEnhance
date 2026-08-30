# sync-code-to-doc 模块生命周期增强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 `harness/skills/sync-code-to-doc/`，使其支持结构性重构（模块删除/迁移）的文档同步——新增 `REMOVE-DOC` / `MIGRATE-内容` 动作类型、README 索引兜底规则、语义重写显式化，以及脚本 `--check-docs` 文档索引一致性检查。

**Architecture:** 脚本层新增纯函数式 `--check-docs` 检查器（死索引/漏索引/断链/孤儿），可注入 docs 根路径便于测试；SKILL.md 规则层新增模块生命周期动作类型与文档级影响判定步骤，作为 AI 执行规则。二者解耦：脚本管机械检查，规则管语义执行。

**Tech Stack:** Python 3（脚本）、unittest（测试，遵循 harness/skills/git-works-push/tests 惯例）、Markdown 规则文档（SKILL.md）。

**前置状态确认（执行前验证）：**
- 当前分支 dev，工作区干净
- spec 已评审通过：`docs/superpowers/specs/2026-08-30-sync-code-to-doc-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `harness/skills/sync-code-to-doc/sync_code_to_doc.py` | Modify | 新增 `--check-docs` / `--docs-root` 参数 + 4 个纯函数 + CLI 集成 + 退出码 5 |
| `harness/skills/sync-code-to-doc/tests/test_check_docs.py` | Create | `--check-docs` 四类检查的单元测试（tmp docs 树，unittest） |
| `harness/skills/sync-code-to-doc/SKILL.md` | Modify | 新动作类型 / 文档级影响判定 / README 兜底 / 语义重写 / 退出码 5 |

**提交纪律：** 每个 Task 末尾的 commit 步骤须经用户确认后由 git-works-push skill 执行（AGENTS.md：禁止未经确认提交）。

---

### Task 1: 死索引检测（TDD）

**Files:**
- Create: `harness/skills/sync-code-to-doc/tests/test_check_docs.py`
- Modify: `harness/skills/sync-code-to-doc/sync_code_to_doc.py`

- [ ] **Step 1: 写失败测试**（仅死索引用例，先建测试骨架）

创建 `harness/skills/sync-code-to-doc/tests/test_check_docs.py`：

```python
#!/usr/bin/env python3
"""sync_code_to_doc --check-docs 单元测试（tmp docs 树，不依赖 git）。

约定：docs 树以 docs_root 为根，README.md 用 ./xxx.md 相对链接索引子文档。
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync_code_to_doc import (
    check_dead_index,
)


def make_docs(tmp: Path, files: dict[str, str]) -> Path:
    """按相对路径 dict 构造 docs 树，返回 docs_root。"""
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp


class TestCheckDeadIndex(unittest.TestCase):
    def test_dead_index_detected(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        dead = check_dead_index(readmes)
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0][1], "missing.md")

    def test_no_dead_index(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        self.assertEqual(check_dead_index(readmes), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -20`

预期：`ImportError: cannot import name 'check_dead_index'`（函数尚未实现）

- [ ] **Step 3: 实现死索引检测**

在 `harness/skills/sync-code-to-doc/sync_code_to_doc.py` 顶部（`_git` 函数之前）新增：

```python
import re

MARKDOWN_LINK_RE = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


def _iter_docs_md(docs_root: Path) -> list[Path]:
    """遍历 docs 根下业务 .md（排除 superpowers 目录），按路径排序。"""
    return sorted(
        p for p in docs_root.rglob("*.md")
        if "superpowers" not in p.parts
    )


def check_dead_index(readmes: list[Path]) -> list[tuple[Path, str]]:
    """README 索引中 ./xxx.md 链接指向不存在的文件 → 死索引。

    返回 [(README路径, 失效链接相对名)]。仅检查 ./ 相对链接，忽略 http/https。
    """
    dead: list[tuple[Path, str]] = []
    for readme in readmes:
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            if not (readme.parent / rel).exists():
                dead.append((readme, rel))
    return dead
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -20`

预期：`Ran 2 tests ... OK`

- [ ] **Step 5: 提交（须用户确认，走 git-works-push）**

---

### Task 2: 漏索引 / 断链 / 孤儿检测（TDD）

**Files:**
- Modify: `harness/skills/sync-code-to-doc/tests/test_check_docs.py`
- Modify: `harness/skills/sync-code-to-doc/sync_code_to_doc.py`

- [ ] **Step 1: 追加失败测试**

在 `harness/skills/sync-code-to-doc/tests/test_check_docs.py` 的 `if __name__` 之前追加（并将顶部 import 扩展为）：

```python
# 顶部 import 改为：
from sync_code_to_doc import (
    check_dead_index,
    check_missing_index,
    check_broken_links,
    check_orphans,
)


class TestCheckMissingIndex(unittest.TestCase):
    def test_missing_index_detected(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
            "01-x/absent.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        missing = check_missing_index(docs, readmes)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].name, "absent.md")

    def test_no_missing_index(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        self.assertEqual(check_missing_index(docs, readmes), [])


class TestCheckBrokenLinks(unittest.TestCase):
    def test_broken_links_detected(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/a.md": "见 [缺失](./gone.md)\n",
            "01-x/b.md": "内容\n",
        })
        broken = check_broken_links(docs)
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0][1], "gone.md")

    def test_no_broken_links(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/a.md": "见 [存在](./b.md)\n",
            "01-x/b.md": "内容\n",
        })
        self.assertEqual(check_broken_links(docs), [])


class TestCheckOrphans(unittest.TestCase):
    def test_orphan_detected(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
            "01-x/lost.md": "无人引用\n",
        })
        orphans = check_orphans(docs)
        self.assertEqual([p.name for p in orphans], ["lost.md"])

    def test_no_orphan(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "见 [readme](./README.md)\n",
        })
        self.assertEqual(check_orphans(docs), [])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -30`

预期：`ImportError: cannot import name 'check_missing_index'`（函数未实现）

- [ ] **Step 3: 实现三个检测函数**

在 `sync_code_to_doc.py` 中 `check_dead_index` 之后追加：

```python
def _readme_linked_targets(readmes: list[Path]) -> set[Path]:
    """收集所有 README 中 ./xxx.md 链接解析后的绝对路径。"""
    linked: set[Path] = set()
    for readme in readmes:
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            linked.add((readme.parent / rel).resolve())
    return linked


def check_missing_index(docs_root: Path, readmes: list[Path]) -> list[Path]:
    """docs 下存在 .md 且所在目录有 README，但未被该 README 链接 → 漏索引。"""
    linked = _readme_linked_targets(readmes)
    missing: list[Path] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        if (md.parent / "README.md").exists() and md.resolve() not in linked:
            missing.append(md)
    return missing


def check_broken_links(docs_root: Path) -> list[tuple[Path, str]]:
    """docs 下非 README 正文的 ./xxx.md 链接目标不存在 → 断链。

    README 的死链已由 check_dead_index 覆盖，此处排除避免重复。
    """
    broken: list[tuple[Path, str]] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            if not (md.parent / rel).exists():
                broken.append((md, rel))
    return broken


def check_orphans(docs_root: Path) -> list[Path]:
    """docs 下非 README 的 .md，无任何 md（含 README）入链 → 孤儿文档。"""
    referenced: set[Path] = set()
    for md in _iter_docs_md(docs_root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            referenced.add((md.parent / rel).resolve())
    orphans: list[Path] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        if md.resolve() not in referenced:
            orphans.append(md)
    return orphans
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -30`

预期：`Ran 8 tests ... OK`

- [ ] **Step 5: 提交（须用户确认，走 git-works-push）**

---

### Task 3: CLI 集成 `--check-docs` + 退出码 5（TDD）

**Files:**
- Modify: `harness/skills/sync-code-to-doc/tests/test_check_docs.py`
- Modify: `harness/skills/sync-code-to-doc/sync_code_to_doc.py`

- [ ] **Step 1: 追加失败测试（CLI 子进程级，含 cmd_check_docs 单测）**

在 `harness/skills/sync-code-to-doc/tests/test_check_docs.py` 的 `if __name__` 之前追加（并将顶部 import 扩展为）：

```python
# 顶部 import 改为：
from sync_code_to_doc import (
    check_dead_index,
    check_missing_index,
    check_broken_links,
    check_orphans,
    cmd_check_docs,
)


class TestCmdCheckDocs(unittest.TestCase):
    def test_consistent_exits_0(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        self.assertEqual(cmd_check_docs(docs), 0)

    def test_inconsistent_exits_5(self):
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
            "01-x/real.md": "内容\n",
        })
        self.assertEqual(cmd_check_docs(docs), 5)

    def test_cli_flag_consistent(self):
        import subprocess
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        script = Path(__file__).resolve().parents[1] / "sync_code_to_doc.py"
        r = subprocess.run(
            ["python3", str(script), "--check-docs", "--docs-root", str(docs)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("一致", r.stdout)

    def test_cli_flag_inconsistent(self):
        import subprocess
        docs = make_docs(Path(tempfile.mkdtemp()), {
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
        })
        script = Path(__file__).resolve().parents[1] / "sync_code_to_doc.py"
        r = subprocess.run(
            ["python3", str(script), "--check-docs", "--docs-root", str(docs)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 5)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -30`

预期：`ImportError: cannot import name 'cmd_check_docs'` / CLI 报 unrecognized argument

- [ ] **Step 3: 实现 cmd_check_docs + CLI 集成**

在 `sync_code_to_doc.py` 中 `check_orphans` 之后追加：

```python
def _render_check_report(docs_root: Path, dead, missing, broken, orphans) -> None:
    """输出 --check-docs 报告。"""
    print("")
    print("========== 文档索引一致性检查（%s） ==========" % docs_root)
    if dead:
        print("\n[死索引] README 引用 docs 下不存在的文件:")
        for readme, rel in dead:
            print(f"  {readme}  →  ./{rel}")
    if missing:
        print("\n[漏索引] docs 下存在但未被所在目录 README 链接:")
        for md in missing:
            print(f"  {md}")
    if broken:
        print("\n[断链] 正文引用不存在的目标:")
        for md, rel in broken:
            print(f"  {md}  →  ./{rel}")
    if orphans:
        print("\n[孤儿] docs 下无任何入链的文档:")
        for md in orphans:
            print(f"  {md}")


def cmd_check_docs(docs_root: Path) -> int:
    """执行文档索引一致性检查，返回退出码（0=一致；5=不一致）。"""
    readmes = sorted(docs_root.rglob("README.md"))
    dead = check_dead_index(readmes)
    missing = check_missing_index(docs_root, readmes)
    broken = check_broken_links(docs_root)
    orphans = check_orphans(docs_root)

    _render_check_report(docs_root, dead, missing, broken, orphans)

    if not (dead or missing or broken or orphans):
        print("\n一致：无死索引 / 漏索引 / 断链 / 孤儿。")
        return 0
    return 5
```

在 `main()` 中（`args = parser.parse_args()` 之后、`harness_init` 之前）新增参数与分派：

```python
    parser.add_argument(
        "--check-docs",
        action="store_true",
        help="仅执行文档索引一致性检查（死索引/漏索引/断链/孤儿），不依赖 git diff",
    )
    parser.add_argument(
        "--docs-root",
        default=None,
        help="docs 根目录（默认仓库 docs/；测试可覆盖）",
    )
    args = parser.parse_args()

    if args.check_docs:
        harness_init("sync_code_to_doc")
        docs_root = Path(args.docs_root) if args.docs_root else repo_root() / "docs"
        if not docs_root.is_dir():
            log_error(f"docs 根目录不存在: {docs_root}")
            harness_exit(3)
        code = cmd_check_docs(docs_root)
        harness_exit(code)
```

同时在 `--check-only` 的"下一步"提示文案末尾追加一行说明（`--check-docs` 用法）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest harness.skills.sync-code-to-doc.tests.test_check_docs -v 2>&1 | tail -30`

预期：`Ran 12 tests ... OK`

- [ ] **Step 5: 提交（须用户确认，走 git-works-push）**

---

### Task 4: SKILL.md 规则增强（模块生命周期）

**Files:**
- Modify: `harness/skills/sync-code-to-doc/SKILL.md`

- [ ] **Step 1: 更新 Inputs 表（新增参数行）**

在 SKILL.md 的 `--check-only / --dry-run` 行后追加：

```markdown
| `--check-docs` | 仅执行文档索引一致性检查（死索引/漏索引/断链/孤儿），不依赖 git diff；配合 `--docs-root` 可覆盖 docs 根目录（测试用） |
```

- [ ] **Step 2: 扩展动作类型表（§5.2）**

在 `REMOVE-文件` 行后追加两行：

```markdown
| `REMOVE-DOC` | 整篇删除文档：删除文件 + README 索引清理 + 全仓交叉引用清理 + 断链自检（git 历史即归档，不额外备份） | **强制确认**（同 DOC-CONFLICT） |
| `MIGRATE-内容` | 跨文档章节/文件引用迁移（源→目标），如文件重命名跨目录后其模块分解/职责矩阵随迁 | **强制确认** |
```

- [ ] **Step 3: 新增"文档级影响判定"（Step 4 前置，插入 Step 4 小节开头）**

在 SKILL.md "### 4. 定位受影响文档章节" 标题后插入：

```markdown
**文档级影响判定（先行）**：对 diff 先判定结构性变化还是增量变化——

| 判定信号 | 文档级影响 | 动作 |
|---------|-----------|------|
| 服务目录整删（`hal/`、`aidl_api/`、`*-service.xml`、`*.rc`、sepolicy `*_hal.te` 同批删除） | 模块消失 | `REMOVE-DOC` |
| 文件重命名跨目录（如 `hal/DeviceReader.cpp → daemon/`） | 内容归属变更 | `MIGRATE-内容` |
| 单文件行号漂移/签名/常量变化 | 无 | 现有增量机制（UPDATE-*） |

判定依据：diff 状态（A/M/D/R）+ 路径模式 + 同批删除关联性。判定为结构性变化时，优先处置文档级动作（REMOVE-DOC/MIGRATE-内容），再对剩余变更点走下方 5 种形态定位。
```

- [ ] **Step 4: 语义失效章节重写显式化（§5.2 动作类型表后追加说明）**

在动作类型表后追加：

```markdown
> **语义失效重写**：架构变化导致章节语义失效（如 HAL 服务被直读内核替代后，"HAL 服务未就绪 / Binder 连接中断"章节失效），整段重写仍属 `UPDATE-文本`，但必须在动作清单中显式标注"语义重写"+失效原因，列入动作清单由用户确认。
```

- [ ] **Step 5: 更新 Step 6 落盘说明（README 兜底）**

在 SKILL.md "### 6. 落盘" 的"禁止"行后追加：

```markdown
- 执行 `REMOVE-DOC` / `ADD-DOC` / `RENAME-DOC` 后**强制同步 README 索引**（文档列表条目、架构图组件、链路描述），避免死索引/断链
- 删除文档前用 `grep -rn "<文档名>" docs/` 全量清理交叉引用
```

- [ ] **Step 6: 更新 Step 7 自检表（新增 --check-docs 项）**

在 Step 7 自检表末尾追加一行：

```markdown
| 文档索引一致性 | `python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-docs`（死索引/漏索引/断链/孤儿） | 退出码 5 时逐一修复 |
```

- [ ] **Step 7: 更新"不涉及的文档"章节（README 兜底条件化）**

将 SKILL.md "## 不涉及的文档" 整节替换为：

```markdown
## 不涉及的文档

- `code/rpi5/README.md` 文件映射表由 AI 基于 manifest.yaml 维护，不纳入本流程；报告 `(root)` 组中的 `README.md` / `manifest.yaml` 变动属噪音，忽略。
- `docs/*/README.md` 索引：**日常增量文本更新不纳入**；但当文档发生结构变化（`REMOVE-DOC` / `ADD-DOC` / `RENAME-DOC`）时，**强制同步**对应 README 索引（文档列表条目、架构图组件、链路描述）。
```

- [ ] **Step 8: 更新退出码表（新增 5）**

在 SKILL.md 退出码表追加：

```markdown
| 5 | 文档索引一致性检查发现不一致（`--check-docs`） | 按报告逐一修复（死索引/漏索引/断链/孤儿） |
```

- [ ] **Step 9: 更新约束表（新增 2 行）**

在 SKILL.md 约束表追加：

```markdown
| 文档结构变化兜底 | 删除/新增/重命名文档时强制同步 README 索引与交叉引用（`REMOVE-DOC` / `MIGRATE-内容` 强制确认） |
| 文档索引一致 | 落盘后 `--check-docs` 通过（无死索引/漏索引/断链/孤儿） |
```

- [ ] **Step 10: 提交（须用户确认，走 git-works-push）**

---

### Task 5: 集成验证

**Files:**（无改动，仅验证）

- [ ] **Step 1: 全量单元测试**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 -m unittest discover -s harness/skills/sync-code-to-doc/tests -v 2>&1 | tail -10`

预期：`Ran 12 tests ... OK`

- [ ] **Step 2: 现有报告功能回归**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-only --base origin/main 2>&1 | tail -8`

预期：与增强前输出一致（40 个文件变动，退出码 0），无回归。

- [ ] **Step 3: 实跑 --check-docs（当前 docs 现状基线）**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-docs; echo "exit=$?"`

预期：报出当前既有问题（如 02 README 的 02.02/02.03 无链接 → 漏索引；docs/development-tools.md 无入链 → 孤儿），并给出退出码 5。**这些是历史遗留问题，不属于本次改动范围**，记录后作为后续文档清理输入。

- [ ] **Step 4: 应用验证——生成 lcview 去 HAL 文档同步方案（手工确认）**

Run: `cd /mnt/d/Code/Github/AndroidSystemEnhance && python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --full-diff --base origin/main`

按增强后的 SKILL.md 流程走：文档级影响判定 → `REMOVE-DOC(01.02)` + `MIGRATE-内容(DeviceReader→01.03)` + 01.03 语义重写 + README 索引兜底，输出动作清单级方案，**经用户确认后落盘**（落盘属于文档同步应用阶段，本计划仅验证方案可生成）。

- [ ] **Step 5: 提交（须用户确认，走 git-works-push）**

---

## 自检

**Spec 覆盖：**
- G1（REMOVE-DOC）→ Task 4 Step 2/3/5 ✓
- G2（MIGRATE-内容）→ Task 4 Step 2/3 ✓
- G3（README 兜底）→ Task 4 Step 5/7 ✓
- 语义重写显式化 → Task 4 Step 4 ✓
- 脚本 --check-docs 四检查 → Task 1/2/3 ✓
- 退出码 5 → Task 3 + Task 4 Step 8 ✓
- 本次场景验证 → Task 5 Step 4 ✓

**占位符扫描：** 全部代码块完整，无 TBD/TODO。

**类型一致性：** `cmd_check_docs(docs_root) -> int`（Task 3 定义，Task 1 测试 import 一致）；`check_dead_index/check_missing_index/check_broken_links/check_orphans` 签名在 Task 1/2 定义、Task 1/2 测试引用一致。
