"""check_lcview_events 单测：lcview 事件 schema 与内核发射点契约比对判定。

覆盖：id 一致通过 / schema id 与内核宏漂移判红 / 字段类型序漂移判红 /
内核无发射点判红 / schema 解析异常判红（CDP-DOD-001 破坏即判红要件）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_lcview_events import (EVENTS_H_REL, SCHEMA_REL,  # noqa: E402
                                 compare, parse_emit_sequences,
                                 parse_event_id_macros, parse_schema)

_SCHEMA_OK = """{
  "version": 1,
  "events": [
    {"id": 4, "name": "usb_transport_start",
     "fields": [{"name": "device_index", "type": "int64"},
                {"name": "data_direction", "type": "int64"},
                {"name": "bytes_to_xfer", "type": "int64"}]}
  ]
}"""

_EVENTS_H_OK = """#ifndef LCVIEW_EVENTS_H
#define LCVIEW_EVENTS_H
#define LCVIEW_EVENT_USB_TRANSPORT_START 4  /* USB transport start */
#endif
"""

_EMIT_OK = """static void trace(void)
{
    struct lcview_builder *b;
    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, 0);
    if (!b)
        return;
    rc = lcview_builder_add_int(b, (int64_t)idx);
    rc |= lcview_builder_add_int(b, (int64_t)dir);
    rc |= lcview_builder_add_int(b, (int64_t)bytes);
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}
"""


class TestParse(unittest.TestCase):
    def test_parse_schema(self):
        evs = parse_schema(_SCHEMA_OK)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["id"], 4)
        self.assertEqual(evs[0]["fields"], ["int64", "int64", "int64"])

    def test_parse_schema_bad_json(self):
        with self.assertRaises(ValueError):
            parse_schema("not json")

    def test_parse_event_id_macros(self):
        macros = parse_event_id_macros(_EVENTS_H_OK)
        self.assertEqual(macros, {"LCVIEW_EVENT_USB_TRANSPORT_START": 4})

    def test_parse_emit_sequences(self):
        emit = parse_emit_sequences(_EMIT_OK)
        self.assertEqual(emit,
                         {"LCVIEW_EVENT_USB_TRANSPORT_START":
                          ["int64", "int64", "int64"]})


class TestCompare(unittest.TestCase):
    def _repo(self, schema=_SCHEMA_OK, events_h=_EVENTS_H_OK,
              emit=_EMIT_OK):
        """搭临时 code 仓根（按相对路径摆文件），返回 repo Path。"""
        d = Path(tempfile.mkdtemp(prefix="lcview_events_"))
        (d / Path(SCHEMA_REL).parent).mkdir(parents=True, exist_ok=True)
        (d / Path(EVENTS_H_REL).parent).mkdir(parents=True, exist_ok=True)
        for rel in ("rpi5/kernel/new/vendor/lechao/LcIod",
                    "rpi5/kernel/new/vendor/lechao/LcIod"):
            (d / Path(rel)).mkdir(parents=True, exist_ok=True)
        (d / Path(SCHEMA_REL)).write_text(schema, encoding="utf-8")
        (d / Path(EVENTS_H_REL)).write_text(events_h, encoding="utf-8")
        (d / "rpi5/kernel/new/vendor/lechao/LcIod"
         / "lciod_usbd-stats.c").write_text(emit, encoding="utf-8")
        (d / "rpi5/kernel/new/vendor/lechao/LcIod"
         / "lciod_usbd.c").write_text("/* no emits */\n", encoding="utf-8")
        return d

    def _cleanup(self, d):
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_consistent_returns_ok(self):
        d = self._repo()
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)

    def test_id_drift_returns_red(self):
        # schema id=5 而内核宏=4 → 判红（id 契约破坏）
        schema = _SCHEMA_OK.replace('"id": 4', '"id": 5')
        d = self._repo(schema=schema)
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("id=5", msg)
        self.assertIn("!= 内核宏", msg)

    def test_field_type_order_drift_returns_red(self):
        # 内核发射点字段序变（少一个字段）→ schema 序不匹配判红
        emit = _EMIT_OK.replace(
            "    rc |= lcview_builder_add_int(b, (int64_t)bytes);\n", "")
        d = self._repo(emit=emit)
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("字段类型序漂移", msg)

    def test_emit_missing_returns_red(self):
        # 内核无该事件发射点 → 判红（schema 定义的事件无处产生）
        d = self._repo(emit="/* no lcview emits */\n")
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("无", msg)

    def test_missing_file_returns_2(self):
        d = self._repo()
        try:
            (d / Path(SCHEMA_REL)).unlink()
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 2)
        self.assertIn("文件缺失", msg)

    # ============================================================
    # R-04 方向 3：双向互查 + commit 段界 + 零字段事件不误报
    # ============================================================

    def test_kernel_emit_missing_from_schema_returns_red(self):
        # 反向契约：内核有发射点宏（NEW_EVENT）而 schema 无此事件 → 判红
        # （schema 漏加新事件，用户态解析时无 id/字段定义）
        schema = _SCHEMA_OK
        events_h = _EVENTS_H_OK
        emit = _EMIT_OK.replace(
            "b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, 0);\n",
            "b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, 0);\n"
            "    b2 = lcview_builder_start(LCVIEW_EVENT_USB_NEW_EVENT, 0);\n"
            "    lcview_builder_add_int(b2, (int64_t)1);\n"
            "    lcview_builder_commit(b2, &lcview_ring);\n")
        d = self._repo(schema=schema, events_h=events_h, emit=emit)
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("无对应 schema 事件", msg)
        self.assertIn("LCVIEW_EVENT_USB_NEW_EVENT", msg)

    def test_zero_field_event_no_false_red(self):
        # 零字段事件：start 后到 commit 前无 add_* → emit 记 []，schema 事件
        # 字段为空 [] 应一致通过（不误报"内核无发射点"）
        schema = _SCHEMA_OK.replace(
            '"fields": [{"name": "device_index", "type": "int64"},\n'
            '                {"name": "data_direction", "type": "int64"},\n'
            '                {"name": "bytes_to_xfer", "type": "int64"}]}',
            '"fields": []}')
        emit = (
            "static void trace(void)\n"
            "{\n"
            "    struct lcview_builder *b;\n"
            "    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, 0);\n"
            "    if (!b)\n"
            "        return;\n"
            "    lcview_builder_commit(b, &lcview_ring);\n"
            "}\n"
        )
        d = self._repo(schema=schema, emit=emit)
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)

    def test_commit_boundary_truncates_segment(self):
        # 段界按 _COMMIT_RE 截断：start 后第一个 commit 之后的其他发射点 add
        # 不得混入本事件字段序（原实现截到 next start，commit 后到 next start
        # 之间的 add 会污染字段序）
        emit = (
            "static void trace(void)\n"
            "{\n"
            "    struct lcview_builder *b;\n"
            "    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, 0);\n"
            "    lcview_builder_add_int(b, (int64_t)1);\n"
            "    lcview_builder_commit(b, &lcview_ring);\n"
            "    /* 已 commit 的事件再 add 属无效（不入字段序） */\n"
            "    lcview_builder_add_str(b, \"oops\");\n"
            "}\n"
        )
        emit = parse_emit_sequences(emit)
        self.assertEqual(emit["LCVIEW_EVENT_USB_TRANSPORT_START"],
                         ["int64"])
        self.assertNotIn("string", emit["LCVIEW_EVENT_USB_TRANSPORT_START"])


if __name__ == "__main__":
    unittest.main()
