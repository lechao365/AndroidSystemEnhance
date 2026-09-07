"""横切守卫：harness 全部 py 的 subprocess.run 调用编码一致性。

背景：Windows（emit 侧）默认按 gbk 解码子进程输出，中文输出遇非法字节
时 stdout 会静默变 None（报错现场丢失）。凡 capture_output/text=True 的
调用必须显式 encoding="utf-8"（建议同时 errors="replace"），本守卫用例
扫描 harness 全部 py 强制执行，防新增调用再次漂移。
"""

import re
import sys
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]


def _violations(root: Path) -> list[str]:
    """返回 root 下 subprocess.run 块内 text=True 缺 encoding 的位置描述。"""
    hits: list[str] = []
    # tst-03：\s* 容忍 subprocess . run ( 空白变体（此前子串 subprocess\.run\(
    # 会被 `subprocess.run (`、`subprocess . run(` 等风格漂移绕过；别名引入
    # 形态 `from subprocess import run` 仍为正则盲区，属文档化局限）
    call_re = re.compile(r"subprocess\s*\.\s*run\s*\(")
    for f in sorted(root.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        txt = f.read_text(encoding="utf-8")
        for m in call_re.finditer(txt):
            i = m.end()
            depth = 1
            while i < len(txt) and depth:
                if txt[i] == "(":
                    depth += 1
                elif txt[i] == ")":
                    depth -= 1
                i += 1
            block = txt[m.start():i]
            if "text=True" in block and "encoding" not in block:
                line = txt[:m.start()].count("\n") + 1
                hits.append(f"{f.relative_to(root)}:{line}")
    return hits


class TestSubprocessEncoding(unittest.TestCase):
    def test_text_true_always_has_encoding(self):
        # text=True 而 encoding 缺失 → Windows 按 gbk 解码，中文输出可致
        # stdout=None（静默丢现场）；全 harness py 逐调用括号配平扫描
        self.assertEqual(_violations(HARNESS_ROOT), [])

    def test_guard_detects_violation(self):
        # 守卫自身有效性：构造违规样例必须被检出（防守卫恒绿的假绿）。
        # 样例文本拆分书写（text= + True），防守卫扫描命中自身文件
        import tempfile
        for sample in (
                'subprocess.run(["ls"], capture_output=True, text=' + 'True)\n',
                # tst-03：空白变体同样须检出
                'subprocess . run(["ls"], capture_output=True, text=' + 'True)\n',
                'subprocess.run (["ls"], capture_output=True, text=' + 'True)\n'):
            with self.subTest(sample=sample[:24]):
                with tempfile.TemporaryDirectory() as d:
                    bad = Path(d) / "bad.py"
                    bad.write_text("import subprocess\n" + sample,
                                   encoding="utf-8")
                    self.assertEqual(_violations(Path(d)), ["bad.py:2"])
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.py"
            good.write_text(
                'import subprocess\n'
                'subprocess.run(["ls"], capture_output=True, text=' + 'True,\n'
                '               encoding="utf-8", errors="replace")\n',
                encoding="utf-8")
            self.assertEqual(_violations(Path(d)), [])


if __name__ == "__main__":
    unittest.main()
