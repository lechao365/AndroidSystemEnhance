#!/usr/bin/env python3
"""sync_code_to_doc --check-docs 单元测试（tmp docs 树，不依赖 git）。

约定：docs 树以 docs_root 为根，README.md 用 ./xxx.md 相对链接索引子文档。
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_code_to_doc as sd  # noqa: E402
from sync_code_to_doc import (
    check_dead_index,
    check_missing_index,
    check_broken_links,
    check_orphans,
    check_code_links,
    check_anchor_bounds,
    check_code_comments,
    cmd_check_docs,
)

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def tearDownModule():
    """清理测试构造的临时 docs 树。"""
    for td in _TMP_DIRS:
        td.cleanup()


def make_docs(files: dict[str, str]) -> Path:
    """按相对路径 dict 构造临时 docs 树，注册清理后返回 docs_root。"""
    td = tempfile.TemporaryDirectory()
    _TMP_DIRS.append(td)
    tmp = Path(td.name)
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp


class TestCheckDeadIndex(unittest.TestCase):
    def test_dead_index_detected(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        dead = check_dead_index(readmes)
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0][1], "missing.md")

    def test_no_dead_index(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        self.assertEqual(check_dead_index(readmes), [])

    def test_http_links_ignored(self):
        docs = make_docs({
            "01-x/README.md": "见 [外部](https://example.com/doc.md)\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        self.assertEqual(check_dead_index(readmes), [])


class TestCheckMissingIndex(unittest.TestCase):
    def test_missing_index_detected(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
            "01-x/absent.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        missing = check_missing_index(docs, readmes)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].name, "absent.md")

    def test_no_missing_index(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        readmes = sorted(docs.rglob("README.md"))
        self.assertEqual(check_missing_index(docs, readmes), [])


class TestCheckBrokenLinks(unittest.TestCase):
    def test_broken_links_detected(self):
        docs = make_docs({
            "01-x/a.md": "见 [缺失](./gone.md)\n",
            "01-x/b.md": "内容\n",
        })
        broken = check_broken_links(docs)
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0][1], "gone.md")

    def test_no_broken_links(self):
        docs = make_docs({
            "01-x/a.md": "见 [存在](./b.md)\n",
            "01-x/b.md": "内容\n",
        })
        self.assertEqual(check_broken_links(docs), [])


class TestCheckOrphans(unittest.TestCase):
    def test_orphan_detected(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
            "01-x/lost.md": "无人引用\n",
        })
        orphans = check_orphans(docs)
        self.assertEqual([p.name for p in orphans], ["lost.md"])

    def test_no_orphan(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "见 [readme](./README.md)\n",
        })
        self.assertEqual(check_orphans(docs), [])


class TestCmdCheckDocs(unittest.TestCase):
    def test_consistent_exits_0(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_check_docs(docs), 0)

    def test_inconsistent_exits_5(self):
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
            "01-x/real.md": "内容\n",
        })
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_check_docs(docs), 5)

    def test_cli_flag_consistent(self):
        import subprocess
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [存在](./real.md) |\n",
            "01-x/real.md": "内容\n",
        })
        script = Path(__file__).resolve().parents[1] / "sync_code_to_doc.py"
        r = subprocess.run(
            [sys.executable, str(script), "--check-docs", "--docs-root", str(docs)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("一致", r.stdout)

    def test_cli_flag_inconsistent(self):
        import subprocess
        docs = make_docs({
            "01-x/README.md": "| 01.01 | [缺失](./missing.md) |\n",
        })
        script = Path(__file__).resolve().parents[1] / "sync_code_to_doc.py"
        r = subprocess.run(
            [sys.executable, str(script), "--check-docs", "--docs-root", str(docs)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 5)

    def test_cli_missing_docs_root_exits_3(self):
        import subprocess
        script = Path(__file__).resolve().parents[1] / "sync_code_to_doc.py"
        r = subprocess.run(
            [sys.executable, str(script), "--check-docs", "--docs-root", "/nonexistent/xyz"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 3)


class TestCheckCodeLinks(unittest.TestCase):
    def test_broken_code_link_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "见源码 [`lcview_main.c`](../../code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c)\n",
        })
        broken = check_code_links(docs)
        self.assertEqual(len(broken), 1)
        self.assertIn("lcview_main.c", broken[0][1])

    def test_valid_code_link_ok(self):
        docs = make_docs({
            "docs/01-x/a.md": "见源码 [`lcview_main.c`](../../code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c)\n",
            "code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c": "内容\n",
        })
        self.assertEqual(check_code_links(docs), [])

    def test_non_code_links_ignored(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [互链](./b.md) 与 [外部](https://example.com/x.cpp)\n",
            "docs/01-x/b.md": "内容\n",
        })
        self.assertEqual(check_code_links(docs), [])


class TestCheckAnchors(unittest.TestCase):
    def test_anchor_out_of_bounds_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [`lcview_main.c:999`](../../code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c#L999)\n",
            "code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c": "第1行\n第2行\n",
        })
        bad = check_anchor_bounds(docs, docs / "code" / "rpi5")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][2], 999)

    def test_anchor_within_bounds_ok(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [`lcview_main.c:2`](../../code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c#L2)\n",
            "code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c": "第1行\n第2行\n",
        })
        self.assertEqual(check_anchor_bounds(docs, docs / "code" / "rpi5"), [])

    def test_code_comment_basename_missing_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// lcview_ghost.c:88\n```\n",
        })
        bad = check_code_comments(docs, docs / "code" / "rpi5")
        self.assertEqual(len(bad), 1)

    def test_code_comment_out_of_bounds_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// lcview_main.c:999\n```\n",
            "code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c": "第1行\n第2行\n",
        })
        bad = check_code_comments(docs, docs / "code" / "rpi5")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][2], 999)

    def test_code_comment_ok(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// lcview_main.c:2\n```\n",
            "code/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c": "第1行\n第2行\n",
        })
        self.assertEqual(check_code_comments(docs, docs / "code" / "rpi5"), [])

    def test_code_comment_cross_platform_found(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// raw-gadget.c:10\n```\n",
            "code/rpi-zero2w/others/usb-fault-inject/raw-gadget.c": "\n" * 20,
        })
        self.assertEqual(check_code_comments(docs, docs / "code"), [])


class TestComputeNumstat(unittest.TestCase):
    """sync-14：多行 numstat 按 \\t 切分精确匹配路径列，后缀同名路径不误命中。"""

    def _numstat(self, stat_path: str, canned: str) -> tuple[str, str]:
        with mock.patch.object(sd, "_git", return_value=canned):
            return sd._compute_numstat(stat_path, stat_path, "M",
                                       Path(tempfile.mkdtemp()), "HEAD")

    def test_same_basename_paths_disambiguated(self):
        # dir/a.py 行在前：旧 endswith 逻辑会把 a.py 误命中 dir/a.py 行
        canned = "2\t0\tdir/a.py\n1\t0\ta.py\n"
        self.assertEqual(self._numstat("a.py", canned), ("1", "0"))
        self.assertEqual(self._numstat("dir/a.py", canned), ("2", "0"))


class TestCodeCommentsIndexDeterminism(unittest.TestCase):
    """sync-15：basename 索引取 sorted 首见，跨目录同名文件归位确定。"""

    def test_same_basename_picks_sorted_first(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// z.c:50\n```\n",
            "code/a/z.c": "1\n",        # sorted 首见（1 行）→ 50 超界应判红
            "code/b/z.c": "\n" * 100,   # 若非确定地取此文件（100 行）则漏报
        })
        bad = check_code_comments(docs, docs / "code")
        self.assertEqual(bad, [(docs / "docs/01-x/a.md", "z.c", 50)])


class TestUndecodableTargetSkipped(unittest.TestCase):
    """sync-08：非 UTF-8/不可读源文件不崩整检——跳过并 log_warn 计数。"""

    def test_anchor_bounds_non_utf8_target_no_crash(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [x](../../code/rpi5/x.c#L2)\n",
        })
        (docs / "code" / "rpi5").mkdir(parents=True, exist_ok=True)
        (docs / "code" / "rpi5" / "x.c").write_bytes(b"\xff\xfe bin")
        with mock.patch.object(sd, "log_warn") as lw:
            bad = check_anchor_bounds(docs, docs / "code")
        self.assertEqual(bad, [])
        self.assertEqual(lw.call_count, 1)

    def test_code_comments_non_utf8_target_no_crash(self):
        docs = make_docs({
            "docs/01-x/a.md": "```c\n// x.c:2\n```\n",
        })
        (docs / "code").mkdir(parents=True, exist_ok=True)
        (docs / "code" / "x.c").write_bytes(b"\xff\xfe bin")
        with mock.patch.object(sd, "log_warn") as lw:
            bad = check_code_comments(docs, docs / "code")
        self.assertEqual(bad, [])
        self.assertEqual(lw.call_count, 1)


class TestCodeLinkRangeAnchor(unittest.TestCase):
    """sync-16：#L10-L20 区间锚点解析 + 上界越界校验。"""

    def test_range_anchor_out_of_bounds_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [f](../../code/rpi5/x.c#L1-L5)\n",
            "code/rpi5/x.c": "l1\nl2\n",
        })
        bad = check_anchor_bounds(docs, docs / "code")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][2], 5)   # 报区间上界行号
        self.assertEqual(bad[0][3], 2)

    def test_range_anchor_within_bounds_ok(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [f](../../code/rpi5/x.c#L1-L2)\n",
            "code/rpi5/x.c": "l1\nl2\n",
        })
        self.assertEqual(check_anchor_bounds(docs, docs / "code"), [])

    def test_single_anchor_still_checked(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [f](../../code/rpi5/x.c#L3)\n",
            "code/rpi5/x.c": "l1\nl2\n",
        })
        bad = check_anchor_bounds(docs, docs / "code")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][2], 3)


class TestCodeLinkMultiPlatform(unittest.TestCase):
    """sync-16：链接过滤放宽为 code/ 前缀——rpi-zero2w 等平台断链也检查。"""

    def test_rpi_zero2w_broken_link_detected(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [g](../../code/rpi-zero2w/others/g.c)\n",
        })
        broken = check_code_links(docs)
        self.assertEqual(len(broken), 1)
        self.assertIn("rpi-zero2w", broken[0][1])

    def test_rpi_zero2w_valid_link_ok(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [g](../../code/rpi-zero2w/others/g.c)\n",
            "code/rpi-zero2w/others/g.c": "x\n",
        })
        self.assertEqual(check_code_links(docs), [])

    def test_rpi5_link_still_checked(self):
        docs = make_docs({
            "docs/01-x/a.md": "见 [f](../../code/rpi5/gone.c)\n",
        })
        self.assertEqual(len(check_code_links(docs)), 1)


class TestCollectChangesGitFailure(unittest.TestCase):
    """sync-07：git 故障 ≠ 真实无变动——git 失败 exit 3，无变动 exit 4。"""

    def _run_main(self, git_return):
        tmp = make_docs({})

        def _exit(code=0):
            raise SystemExit(code)

        with mock.patch.object(sys, "argv", ["sync_code_to_doc.py"]), \
             mock.patch.object(sd, "_git", return_value=git_return), \
             mock.patch.object(sd, "profile_path", return_value=tmp), \
             mock.patch.object(sd, "repo_root", return_value=tmp), \
             mock.patch.object(sd, "harness_init"), \
             mock.patch.object(sd, "harness_exit", side_effect=_exit):
            try:
                sd.main()
            except SystemExit as e:
                return e.code
        return None

    def test_git_failure_exits_3_not_4(self):
        # _git 返 None（命令失败）→ exit 3，不得误判为"无变动" exit 4
        self.assertEqual(self._run_main(None), 3)

    def test_genuine_no_changes_exits_4(self):
        # git 正常（空输出）→ 真实无变动 → exit 4（两路径可区分）
        self.assertEqual(self._run_main(""), 4)


if __name__ == "__main__":
    unittest.main()
