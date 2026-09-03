# ws_forensics.py 单测（方向 4/5）：失败时有界取证——只读命令白名单、
# 单文件/总量上限、tombstone 只取本轮新增、尽力项失败不阻断、manifest。

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_forensics as wf  # noqa: E402


def _fake_adb(seqs):
    """桩 adb_run：按命令首 token 分发返回 (out, rc)。seqs: {key: (out, rc)}"""
    def fake(ep, args, timeout=60):
        key = args[0] if args[0] != "shell" else args[1].split()[0]
        if key == "shell" and args[1].startswith("cat /sys/fs/pstore"):
            key = "pstore-cat"
        if key == "shell" and args[1].startswith("cat /data/tombstones"):
            key = "tombstone-cat"
        if key == "shell" and args[1].startswith("stat -c"):
            key = "tombstone-stat"
        out, rc = seqs.get(key, ("", 0))
        return out, rc
    return fake


# 只读白名单：取证允许下发的全部命令形态（方向 5 只读判定基准）
_READONLY_PREFIXES = (
    ("logcat", "-d"),
    ("shell", "getprop"),
    ("shell", "df"),
    ("shell", "ps"),
    ("shell", "dmesg"),
    ("shell", "echo ok"),
    ("shell", "ls /sys/fs/pstore"),
    ("shell", "cat /sys/fs/pstore/"),
    ("shell", "ls /data/tombstones"),
    ("shell", "stat -c"),
    ("shell", "cat /data/tombstones/"),
)


class TestForensics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "run"
        self._t0 = 1000
        # tombstone：两个文件，一个新增（mtime > since）一个旧（≤ since）
        self.seqs = {
            "logcat": ("--------- crash log\n", 0),
            "getprop": ("[ro.build.version.sdk]: [34]\n", 0),
            "df": ("/dev/root 100% used\n", 0),
            "ps": ("root 1 0 0 init\n", 0),
            "dmesg": ("panic: fake\n", 0),
            "shell": ("t_new.txt\nt_old.txt\n", 0),  # ls /data/tombstones
            "tombstone-stat": ("", -1),  # 由用例按文件名覆盖
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _run_collect(self, ep="ep", **kw):
        with mock.patch.object(wf, "adb_run", side_effect=_fake_adb(self.seqs)):
            return wf.collect(ep=ep, out_dir=str(self.out), **kw)

    def test_collects_device_snapshots_and_host_files(self):
        stdout = self.out.parent / "stdout.txt"
        stdout.write_text("host 失败现场\n", encoding="utf-8")
        manifest, run_dir = self._run_collect(stdout_file=str(stdout))
        names = {i["name"] for i in manifest["items"] if "name" in i}
        self.assertIn("01-stdout.txt", names)
        for rel in ("02-logcat-crash.txt", "03-getprop.txt", "04-df.txt",
                    "05-ps.txt", "06-dmesg.txt"):
            self.assertIn(rel, names)
        self.assertIn("host 失败现场", (run_dir / "01-stdout.txt").read_text(
            encoding="utf-8"))
        self.assertTrue(manifest["run_id"])
        self.assertTrue((run_dir / "manifest.json").is_file())
        # manifest 合法 JSON（原子写产物）
        json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_readonly_commands_only(self):
        # 方向 5：全部命令在只读白名单内（无 setprop/rm/mv/write 类动作）
        seen = []
        orig = _fake_adb(self.seqs)

        def spy(ep, args, timeout=60):
            seen.append(list(args))
            return orig(ep, args, timeout=timeout)

        with mock.patch.object(wf, "adb_run", side_effect=spy):
            wf.collect(ep="ep", out_dir=str(self.out))
        self.assertTrue(seen)
        for args in seen:
            if args[0] == "logcat":
                self.assertIn("-d", args)
                continue
            if args[0] != "shell":
                self.fail(f"非白名单顶层命令: {args}")
            shell_cmd = args[1]
            self.assertTrue(any(shell_cmd.startswith(p)
                                for p in _READONLY_PREFIXES[1:]),
                            f"非只读白名单命令: {shell_cmd}")

    def test_single_file_truncated(self):
        # 方向 5：单文件超 MAX_FILE_BYTES → 截断且 manifest 标记
        big = "x" * (wf.MAX_FILE_BYTES + 1000)
        self.seqs["getprop"] = (big, 0)
        manifest, run_dir = self._run_collect()
        item = [i for i in manifest["items"] if i.get("name") == "03-getprop.txt"][0]
        self.assertTrue(item["truncated"])
        content = (run_dir / "03-getprop.txt").read_text(encoding="utf-8")
        self.assertLessEqual(
            len(content.encode("utf-8")), wf.MAX_FILE_BYTES)

    def test_total_budget_stops_later_items(self):
        # 方向 5：总量上限达到 → 后续项 skipped 且 manifest 如实记录
        self.seqs["logcat"] = ("y" * (wf.MAX_TOTAL_BYTES + 1), 0)
        manifest, _ = self._run_collect()
        by_name = {i.get("name"): i for i in manifest["items"]}
        self.assertTrue(by_name["02-logcat-crash.txt"].get("truncated"))
        for rel in ("03-getprop.txt", "04-df.txt", "05-ps.txt"):
            self.assertEqual(by_name[rel].get("skipped"), "total_budget")

    def test_dmesg_failure_not_fatal(self):
        # 尽力项失败：仅记 error，不算整体失败（collect 正常返回）
        self.seqs["dmesg"] = ("", 1)
        manifest, _ = self._run_collect()
        item = [i for i in manifest["items"]
                if i.get("name") == "06-dmesg.txt"][0]
        self.assertIn("尽力项", item["error"])
        self.assertTrue(manifest["run_id"])

    def test_tombstone_only_fresh(self):
        # 方向 4：tombstone 只取本轮新增（mtime > since_epoch），旧文件跳过
        def fake(ep, args, timeout=60):
            if args[1].startswith("ls /data/tombstones"):
                return "t_new.txt\nt_old.txt\nbad name$.txt\n", 0
            if args[1].startswith("stat -c"):
                name = args[1].rsplit("/", 1)[1]
                mtime = str(self._t0 + 100) if name == "t_new.txt" \
                    else str(self._t0 - 100)
                return f"{mtime} /data/tombstones/{name}", 0
            if args[1].startswith("cat /data/tombstones"):
                return "tombstone content\n", 0
            return _fake_adb(self.seqs)(ep, args, timeout=timeout)
        with mock.patch.object(wf, "adb_run", side_effect=fake):
            manifest, run_dir = wf.collect(ep="ep", out_dir=str(self.out),
                                           since_epoch=self._t0)
        names = {i["name"] for i in manifest["items"] if "name" in i}
        self.assertIn("08-tombstone-t_new.txt", names)
        self.assertNotIn("08-tombstone-t_old.txt", names)
        self.assertNotIn("08-tombstone-bad name$.txt", names)
        self.assertIn("tombstone content",
                      (run_dir / "08-tombstone-t_new.txt").read_text(
                          encoding="utf-8"))

    def test_tombstone_name_whitelist(self):
        # 方向 4 防御：设备侧文件名经白名单过滤（防命令注入），非法名跳过
        def fake(ep, args, timeout=60):
            if args[1].startswith("ls /data/tombstones"):
                return "ok.txt\nx;rm -rf /\n", 0
            if args[1].startswith("stat -c"):
                name = args[1].rsplit("/", 1)[1]
                return f"{self._t0 + 100} /data/tombstones/{name}", 0
            if args[1].startswith("cat /data/tombstones"):
                return "c\n", 0
            return _fake_adb(self.seqs)(ep, args, timeout=timeout)
        with mock.patch.object(wf, "adb_run", side_effect=fake):
            manifest, _ = wf.collect(ep="ep", out_dir=str(self.out),
                                     since_epoch=self._t0)
        names = {i["name"] for i in manifest["items"] if "name" in i}
        self.assertIn("08-tombstone-ok.txt", names)
        # 注入名未触发任何 cat（只允许白名单字符）
        self.assertFalse(any("rm" in n for n in names))

    def test_device_unreachable_still_collects_host(self):
        # 设备不可达 → 设备侧记 error，host 侧现场照收（尽力而为）
        stdout = self.out.parent / "stdout.txt"
        stdout.write_text("仅 host 现场\n", encoding="utf-8")
        with mock.patch.object(wf, "_device_ok", return_value=False):
            manifest, run_dir = wf.collect(ep="ep", out_dir=str(self.out),
                                           stdout_file=str(stdout))
        self.assertTrue(any(i.get("error") == "设备不可达，设备侧快照未采集"
                            "（host 侧现场照收）" for i in manifest["items"]))
        self.assertIn("仅 host 现场", (run_dir / "01-stdout.txt").read_text(
            encoding="utf-8"))

    def test_pstore_best_effort_capped(self):
        # pstore 逐文件 cat，最多 MAX_PSTORE_FILES 个，非法名过滤
        names = [f"p-{i}" for i in range(wf.MAX_PSTORE_FILES + 3)] + ["bad$"]
        def fake(ep, args, timeout=60):
            if args[1].startswith("ls /sys/fs/pstore"):
                return "\n".join(names) + "\n", 0
            if args[1].startswith("cat /sys/fs/pstore"):
                return "ps\n", 0
            return _fake_adb(self.seqs)(ep, args, timeout=timeout)
        with mock.patch.object(wf, "adb_run", side_effect=fake):
            manifest, _ = wf.collect(ep="ep", out_dir=str(self.out))
        pstore_items = [i for i in manifest["items"]
                        if i.get("name", "").startswith("07-pstore-")]
        self.assertEqual(len(pstore_items), wf.MAX_PSTORE_FILES)
        self.assertFalse(any("bad$" in i.get("name", "")
                             for i in pstore_items))


if __name__ == "__main__":
    unittest.main()
