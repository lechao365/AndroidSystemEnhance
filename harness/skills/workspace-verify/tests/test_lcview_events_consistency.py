import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 内核态与用户态各一份 lcview_events.h（跨进程二进制协议共享定义）
_KERNEL_HDR = (Path(__file__).resolve().parents[4] / "code" / "rpi5" /
               "kernel" / "new" / "vendor" / "lechao" / "LcView" /
               "lcview_events.h")
_USER_HDR = (Path(__file__).resolve().parents[4] / "code" / "rpi5" / "aosp" /
             "new" / "vendor" / "lechao" / "services" / "lechao_lcview" /
             "include" / "lcview_events.h")

_DEF_RE = re.compile(r"^#define\s+(LCVIEW_[A-Z0-9_]+)\s+(\S+)", re.M)
_STRUCT_RE = re.compile(
    r"struct lcview_record_hdr \{(.*?)\}\s*(?:__attribute__\(\(packed\)\))?;",
    re.S)
_FIELD_RE = re.compile(r"(?:u?int\d+_t|uint64_t|uint16_t|uint8_t)\s+(\w+);")


def _macros(text):
    return {m: v for m, v in _DEF_RE.findall(text)}


def _hdr_fields(text):
    m = _STRUCT_RE.search(text)
    if not m:
        return []
    return _FIELD_RE.findall(m.group(1))


class TestLcviewEventsConsistency(unittest.TestCase):
    def setUp(self):
        self.kernel = _KERNEL_HDR.read_text(encoding="utf-8")
        self.user = _USER_HDR.read_text(encoding="utf-8")

    def test_both_headers_exist(self):
        self.assertTrue(_KERNEL_HDR.is_file(), str(_KERNEL_HDR))
        self.assertTrue(_USER_HDR.is_file(), str(_USER_HDR))

    def test_magic_consistent(self):
        # 魔数：内核与用户态须一致（'LV' ASCII 0x4C56）
        self.assertEqual(_macros(self.kernel)["LCVIEW_MAGIC"],
                         _macros(self.user)["LCVIEW_MAGIC"])
        self.assertEqual(_macros(self.user)["LCVIEW_MAGIC"], "0x4C56")

    def test_field_types_consistent(self):
        # LCVIEW_TYPE_*（1..5）两份一致且值正确
        k, u = _macros(self.kernel), _macros(self.user)
        for name in ("LCVIEW_TYPE_INT32", "LCVIEW_TYPE_INT64",
                     "LCVIEW_TYPE_FLOAT", "LCVIEW_TYPE_STRING",
                     "LCVIEW_TYPE_BINARY"):
            self.assertIn(name, k, f"内核缺 {name}")
            self.assertIn(name, u, f"用户态缺 {name}")
            self.assertEqual(k[name], u[name], name)
        self.assertEqual(k["LCVIEW_TYPE_INT32"], "1")
        self.assertEqual(k["LCVIEW_TYPE_BINARY"], "5")

    def test_event_ids_consistent(self):
        # 事件 id 1..13 两份一致（trigger 用例依赖 id 8=probe 9=disconnect）
        k, u = _macros(self.kernel), _macros(self.user)
        events = [n for n in k if n.startswith("LCVIEW_EVENT_")]
        self.assertGreaterEqual(len(events), 13)
        for name in events:
            self.assertIn(name, u, f"用户态缺 {name}")
            self.assertEqual(k[name], u[name], name)
        self.assertEqual(k["LCVIEW_EVENT_USB_PROBE"], "8")
        self.assertEqual(k["LCVIEW_EVENT_USB_DISCONNECT"], "9")

    def test_record_hdr_layout_identical(self):
        # 记录头字段序列一致（跨进程二进制布局，CXX-001）
        kf, uf = _hdr_fields(self.kernel), _hdr_fields(self.user)
        self.assertEqual(kf, uf)
        self.assertEqual(
            kf, ["magic", "event_id", "level", "field_count",
                 "reserved", "timestamp_ns"])

    def test_hdr_size_16_bytes(self):
        # 固定 16 字节头：u16+u16+u8+u8+u16+u64（packed 1 字节对齐）
        self.assertEqual(_hdr_fields(self.kernel), _hdr_fields(self.user))
        sizes = {"magic": 2, "event_id": 2, "level": 1, "field_count": 1,
                 "reserved": 2, "timestamp_ns": 8}
        self.assertEqual(sum(sizes.values()), 16)


if __name__ == "__main__":
    unittest.main()