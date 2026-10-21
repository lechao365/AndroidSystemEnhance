"""check_lcview_events 单测：lcview 事件 schema 与内核发射点契约比对判定。

覆盖：id 一致通过 / schema id 与内核宏漂移判红 / 字段类型序漂移判红 /
内核无发射点判红 / schema 解析异常判红（CDP-DOD-001 破坏即判红要件）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_lcview_events import (DISABLED_EVENTS, EMIT_SOURCE_RELS,  # noqa: E402
                                 EVENTS_H_REL,  # noqa: E402
                                 SCHEMA_REL, UT_SINGLE_SOURCE_HELPER,  # noqa: E402
                                 UT_TESTS_REL,  # noqa: E402
                                 compare,  # noqa: E402
                                 parse_emit_sequences,  # noqa: E402
                                 parse_event_id_macros, parse_schema)  # noqa: E402

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
                    "rpi5/kernel/new/vendor/lechao/LcView"):
            (d / Path(rel)).mkdir(parents=True, exist_ok=True)
        (d / Path(SCHEMA_REL)).write_text(schema, encoding="utf-8")
        (d / Path(EVENTS_H_REL)).write_text(events_h, encoding="utf-8")
        # 按 EMIT_SOURCE_RELS 全量摆发射点源文件（首个承载 emit，其余空），
        # 使夹具随检查器发射点清单扩展自洽（A 批一新增 lciod_sd.c）
        for i, rel in enumerate(EMIT_SOURCE_RELS):
            p = d / Path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(emit if i == 0 else "/* no emits */\n",
                         encoding="utf-8")
        # UT 单源夹具：辅助头 + 引用它的单测（不硬编码事件计数）
        ut_dir = d / Path(UT_TESTS_REL)
        ut_dir.mkdir(parents=True, exist_ok=True)
        (ut_dir / UT_SINGLE_SOURCE_HELPER).write_text(
            "// kSchemaConfigPath loadExpectedEvents\n", encoding="utf-8")
        (ut_dir / "SchemaParser_test.cpp").write_text(
            "// loadExpectedEvents\n"
            "EXPECT_EQ(sp.eventCount(), expected.size());\n",
            encoding="utf-8")
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
        # 非停发事件内核无发射点 → 判红（schema 定义的事件无处产生）；
        # usb_transport_start 已登记停发（R-11 方向 3），改用 end 事件验证
        schema = _SCHEMA_OK.replace("usb_transport_start", "usb_transport_end")
        events_h = _EVENTS_H_OK.replace(
            "LCVIEW_EVENT_USB_TRANSPORT_START 4",
            "LCVIEW_EVENT_USB_TRANSPORT_END 5")
        d = self._repo(schema=schema, events_h=events_h,
                       emit="/* no lcview emits */\n")
        try:
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("无", msg)

    def test_disabled_event_missing_emit_allowed(self):
        # R-11 方向 3：登记在 DISABLED_EVENTS 的停发事件，内核不再发射
        # （TRANSPORT_START 降为调试诊断），schema 保留定义 → 跳过判红。
        # R-19 P5 方向 3：id=1~3（USB_CONNECT/GPIO_IRQ/SENSOR_DATA）预留占位
        # 事件登记为豁免——schema 有 reserved 条目但内核无发射点不判红；
        # R3 方向 1/3：SLOW_DISK（id=14）为 daemon BlockCollector 合成事件，
        # 无内核发射点同样豁免。
        # C 批二方向2：USB_LATENCY_TAIL（id=17）为 lciod daemon 延迟直方图
        # 长尾规则合成事件，无内核发射点同样豁免。
        # 方向三批一：ZRAM_SWAP_PRESSURE（id=19）为 lciod daemon zram 换入
        # 压力窗聚合规则合成事件，无内核发射点同样豁免。
        # UP-110 批一：TRIM（id=20）为 lciod daemon TrimManager 分段 FITRIM
        # 规则合成事件，无内核发射点同样豁免。
        # 拔盘韧性守护：WRITEBACK_GUARD（id=21）为 lciod daemon WritebackGuard
        # 60s 巡检合成事件，无内核发射点同样豁免。
        # 集合非空（含预留事件宏），未登记事件缺发射点仍判红（语义收敛到
        # test_non_disabled_missing_emit_still_red）。
        self.assertEqual(DISABLED_EVENTS, {
            "LCVIEW_EVENT_USB_CONNECT",
            "LCVIEW_EVENT_GPIO_IRQ",
            "LCVIEW_EVENT_SENSOR_DATA",
            "LCVIEW_EVENT_SLOW_DISK",
            "LCVIEW_EVENT_USB_LATENCY_TAIL",
            "LCVIEW_EVENT_ZRAM_SWAP_PRESSURE",
            "LCVIEW_EVENT_TRIM",
            "LCVIEW_EVENT_WRITEBACK_GUARD",
        })

    def test_non_disabled_missing_emit_still_red(self):
        # 非停发事件（schema 定义未登记）无发射点 → 仍判红，防登记被滥用
        schema = _SCHEMA_OK.replace("usb_transport_start", "usb_transport_end")
        events_h = _EVENTS_H_OK.replace(
            "LCVIEW_EVENT_USB_TRANSPORT_START 4",
            "LCVIEW_EVENT_USB_TRANSPORT_END 5")
        emit = "/* no lcview emits */\n"
        d = self._repo(schema=schema, events_h=events_h, emit=emit)
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
    # 方向 2：UT 期望事件集合单源（配置生成，禁硬编码计数）
    # CDP-DOD-001：破坏场景（硬编码/辅助缺失/未引用）必须判红
    # ============================================================

    def _ut_dir(self, d):
        return d / Path(UT_TESTS_REL)

    def test_ut_hardcoded_event_count_returns_red(self):
        # UT 重新写死 eventCount(), 20u → 判红（新增事件漏同步风险回潮）
        d = self._repo()
        try:
            (self._ut_dir(d) / "SchemaParser_test.cpp").write_text(
                "// loadExpectedEvents\n"
                "EXPECT_EQ(sp.eventCount(), 20u);\n", encoding="utf-8")
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("硬编码事件计数", msg)

    def test_ut_single_source_helper_missing_returns_red(self):
        # 单源辅助被删 → 判红（UT 无法从配置生成期望值）
        d = self._repo()
        try:
            (self._ut_dir(d) / UT_SINGLE_SOURCE_HELPER).unlink()
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("单源辅助缺失", msg)

    def test_ut_without_single_source_reference_returns_red(self):
        # 辅助存在但 UT 未引用（仍靠别处硬编码）→ 判红
        d = self._repo()
        try:
            for p in self._ut_dir(d).glob("*_test.cpp"):
                p.write_text("// no config reference\n", encoding="utf-8")
            rc, msg = compare(d)
        finally:
            self._cleanup(d)
        self.assertEqual(rc, 1)
        self.assertIn("未引用配置单源辅助", msg)

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
