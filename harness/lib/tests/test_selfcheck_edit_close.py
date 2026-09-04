# selfcheck 编辑收口自动补打（B1，根治 KI-20260902-001）：mark edit 此前由
# apply AI 自判"编辑完成"——-s 批实测漂移（edit mark 打在两轮自检之后，
# 501.9s+315.6s 真实编辑散落 gap）。制度化：selfcheck 开跑前若编辑未收口
# 则自动补打 mark edit（同名自动 #N），编辑区间口径不再依赖 AI 自判。

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# cdp_timing 与 selfcheck 同仓内部复用（打点文件构造/读取断言用）
_CDP_LIB = (Path(__file__).resolve().parents[3] / "harness" / "skills"
            / "cross-device" / "lib" / "python")
sys.path.insert(0, str(_CDP_LIB))

import selfcheck as sc


class TestEnsureEditCloseMark(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def _mk_timing(self, marks):
        """构造活跃批打点文件并指向 current-batch.json，返回 batch_id。"""
        import json

        import cdp_timing
        bid = "abc123def456"
        cdp_timing.main(["start", "--batch", bid])
        p = cdp_timing._timing_path(bid)
        data = json.loads(p.read_text(encoding="utf-8"))
        data["marks"] = marks
        p.write_text(json.dumps(data, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        return bid

    def test_no_edit_mark_backfills(self):
        # 无 edit mark（AI 漏打）→ selfcheck 开跑前自动补打 edit
        bid = self._mk_timing([{"name": "edit_plan", "wall": 100.0}])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        self.assertEqual(m.call_args.args[0][2:6],
                         ["mark", "--batch", bid, "--name"])
        self.assertEqual(m.call_args.args[0][6], "edit")

    def test_edit_before_selfcheck_no_backfill(self):
        # 已有 edit 且在自检之前（正常 AI 手打路径）→ 不重复补打
        self._mk_timing([{"name": "edit", "wall": 100.0}])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        m.assert_not_called()

    def test_edit_stale_after_selfcheck_backfills(self):
        # -s 批漂移形态：edit 打在 apply_selfcheck 之后（真实编辑未收口）
        # → 补打 edit（同名自动 #2），把后续真实编辑区间重新归口
        bid = self._mk_timing([
            {"name": "edit", "wall": 100.0},
            {"name": "apply_selfcheck", "wall": 200.0},
        ])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        self.assertEqual(m.call_args.args[0][6], "edit")
        self.assertIn("--batch", m.call_args.args[0])
        self.assertIn(bid, m.call_args.args[0])

    def test_no_active_batch_skips_silently(self):
        # 无活跃批（emit 侧独立自测等）→ 静默跳过不报错
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
