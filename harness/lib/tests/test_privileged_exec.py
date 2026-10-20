import inspect
import json
import os
import pwd
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privileged_exec as priv


class PrivTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        prefixes = ("LC_PRIVILEGE_", "CDP_")
        self._saved_env = {
            key: value for key, value in os.environ.items()
            if key.startswith(prefixes)
        }
        for key in list(os.environ):
            if key.startswith(prefixes):
                del os.environ[key]
        self.addCleanup(self._restore_env)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "harness" / "config").mkdir(parents=True)
        (self.repo / "harness" / "scripts").mkdir(parents=True)
        self.script = self.repo / "harness" / "scripts" / "demo.sh"
        self.script.write_text("#!/bin/sh\necho demo\n", encoding="utf-8")
        self.script.chmod(0o755)
        self.old_root = priv._ROOT
        priv._ROOT = self.repo
        self.addCleanup(setattr, priv, "_ROOT", self.old_root)
        self.allowlist = (self.repo / "harness" / "config"
                          / "privileged-allowlist.yaml")
        self.write_allowlist()

    def _restore_env(self):
        prefixes = ("LC_PRIVILEGE_", "CDP_")
        for key in list(os.environ):
            if key.startswith(prefixes):
                del os.environ[key]
        os.environ.update(self._saved_env)

    def _no_exec(self):
        """负向测试兜底：若代码意外走到 probe/execute 立即判红。"""
        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            priv, "probe", side_effect=AssertionError("unexpected probe")))
        stack.enter_context(mock.patch.object(
            priv, "_execute",
            side_effect=AssertionError("unexpected execute")))
        return stack

    def write_allowlist(self, extra="", enabled="true", as_root="false",
                        destructive="false", args='["-mode", "0"]',
                        env_passthrough="[HOME, DEMO]",
                        trusted_roots=""):
        requires = "true" if as_root == "true" else "false"
        trusted_block = ""
        if trusted_roots:
            trusted_block = f"    trusted_roots:\n{trusted_roots}\n"
        text = f"""
version: 1
default: deny
entries:
  - id: demo.run
    description: demo
    command: harness/scripts/demo.sh
    interpreter: bash
    args: {args}
    requires_privilege: {requires}
    as_root: {as_root}
    mechanism: auto
    timeout_s: 30
    env_passthrough: {env_passthrough}
{trusted_block}    reason_required: true
    enabled: {enabled}
    destructive: {destructive}
    human_confirm: {destructive}
    approved_by: test
    approved_at: "2026-10-20"
{extra}
"""
        self.allowlist.write_text(text, encoding="utf-8")

    def test_default_deny_required(self):
        self.allowlist.write_text("version: 1\nentries: []\n", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_ALLOWLIST_INVALID")

    def test_enabled_requires_approval(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: x
    command: harness/scripts/demo.sh
    enabled: true
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError):
            priv.load_allowlist()

    def test_unknown_entry(self):
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("nope", ["bash", str(self.script), "-mode", "0"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_UNKNOWN")

    def test_rejection_audit_written(self):
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError):
                priv.run("nope", ["bash", str(self.script), "-mode", "0"],
                         reason="拒绝审计")
        log_dir = self.repo / "harness" / "log" / "privileged"
        audits = [json.loads(path.read_text(encoding="utf-8"))
                  for path in log_dir.glob("*.json")]
        rejects = [item for item in audits
                   if item.get("code") == "PRIV_ENTRY_UNKNOWN"]
        self.assertEqual(len(rejects), 1)
        self.assertIsNone(rejects[0]["rc"])
        self.assertEqual(rejects[0]["reason"], "拒绝审计")
        self.assertEqual(rejects[0]["argv"],
                         ["bash", str(self.script), "-mode", "0"])
        self.assertEqual(rejects[0]["cwd"], "")
        self.assertFalse(rejects[0]["destructive"])

    def test_rejection_audit_records_destructive(self):
        self.write_allowlist(as_root="true", destructive="true")
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="x", cwd=str(self.repo))
        self.assertEqual(cm.exception.code, "PRIV_DESTRUCTIVE_DENIED")
        log_dir = self.repo / "harness" / "log" / "privileged"
        audits = [json.loads(path.read_text(encoding="utf-8"))
                  for path in log_dir.glob("*.json")]
        rejects = [item for item in audits
                   if item.get("code") == "PRIV_DESTRUCTIVE_DENIED"]
        self.assertEqual(len(rejects), 1)
        self.assertTrue(rejects[0]["destructive"])
        self.assertEqual(rejects[0]["cwd"], str(self.repo))

    def test_disabled_entry(self):
        self.write_allowlist(enabled="false")
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_DISABLED")

    def test_disabled_entry_missing_command_still_loads(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: missing.run
    command: harness/scripts/missing.sh
    interpreter: bash
    args: ["-mode", "0"]
    requires_privilege: true
    enabled: false
    destructive: false
""", encoding="utf-8")
        entries = priv.load_allowlist()
        self.assertIn("missing.run", entries)
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("missing.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_DISABLED")

    def test_expired_entry(self):
        self.write_allowlist(extra='    expires_at: "2020-01-01"')
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_EXPIRED")

    def test_arg_mismatch(self):
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run", ["bash", str(self.script), "-mode", "1"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_ARG_MISMATCH")

    def test_command_escape(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: x
    command: ../outside.sh
    args: []
    requires_privilege: false
    enabled: true
    approved_by: t
    approved_at: "2026-10-20"
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_COMMAND_DENIED")

    def test_allowlist_missing(self):
        self.allowlist.unlink()
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_ALLOWLIST_MISSING")

    def test_allowlist_invalid_yaml(self):
        self.allowlist.write_text(
            "version: 1\ndefault: deny\nentries: [\n", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_ALLOWLIST_INVALID")

    def test_load_allowlist_uses_canonical_path(self):
        entries = priv.load_allowlist()
        self.assertIn("demo.run", entries)

    def test_no_runtime_allowlist_override(self):
        self.assertEqual(
            list(inspect.signature(priv.load_allowlist).parameters), [])
        self.assertNotIn(
            "allowlist_path", inspect.signature(priv.run).parameters)

    def test_env_passthrough_denylist_rejected(self):
        names = (
            "LD_PRELOAD", "BASH_ENV", "GCONV_PATH", "IFS",
            "SUDO_ASKPASS", "LOCPATH", "NLSPATH", "MALLOC_ARENA_MAX",
        )
        for name in names:
            with self.subTest(name=name):
                self.allowlist.write_text(f"""
version: 1
default: deny
entries:
  - id: env.run
    command: harness/scripts/demo.sh
    interpreter: bash
    args: []
    requires_privilege: false
    env_passthrough: [{name}]
    enabled: false
""", encoding="utf-8")
                with self.assertRaises(priv.PrivilegeError) as cm:
                    priv.load_allowlist()
                self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_invalid_entry_id_denied(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: bad/id
    command: harness/scripts/demo.sh
    enabled: false
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_entry_id_length_limit(self):
        self.allowlist.write_text(f"""
version: 1
default: deny
entries:
  - id: {"a" * 65}
    command: harness/scripts/demo.sh
    enabled: false
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_trusted_roots_invalid_key_rejected(self):
        self.write_allowlist(trusted_roots="      BAD_ROOT: /ws/aosp")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_trusted_roots_relative_value_rejected(self):
        self.write_allowlist(trusted_roots="      AOSP_WS: relative/aosp")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_trusted_roots_must_be_mapping(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: demo.run
    command: harness/scripts/demo.sh
    interpreter: bash
    args: []
    requires_privilege: false
    trusted_roots: "not-a-map"
    enabled: false
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_SCHEMA")

    def test_version_must_be_one(self):
        self.allowlist.write_text(
            "version: 2\ndefault: deny\nentries: []\n", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_ALLOWLIST_INVALID")

    def test_enabled_missing_command_denied(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: missing.run
    command: harness/scripts/missing.sh
    interpreter: bash
    args: []
    requires_privilege: false
    enabled: true
    approved_by: t
    approved_at: "2026-10-20"
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_COMMAND_DENIED")

    def test_enabled_non_executable_command_denied(self):
        nonx = self.repo / "harness" / "scripts" / "nonx.sh"
        nonx.write_text("#!/bin/sh\necho nonx\n", encoding="utf-8")
        nonx.chmod(0o644)
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: nonx.run
    command: harness/scripts/nonx.sh
    args: []
    requires_privilege: false
    enabled: true
    approved_by: t
    approved_at: "2026-10-20"
""", encoding="utf-8")
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("nonx.run", [str(nonx)], reason="x")
        self.assertEqual(cm.exception.code, "PRIV_COMMAND_DENIED")

    def test_symlink_escape_denied(self):
        outside = Path(self.tmp.name) / "outside.sh"
        outside.write_text("#!/bin/sh\necho outside\n", encoding="utf-8")
        link = self.repo / "harness" / "scripts" / "link.sh"
        os.symlink(outside, link)
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: link.run
    command: harness/scripts/link.sh
    interpreter: bash
    args: []
    requires_privilege: false
    enabled: true
    approved_by: t
    approved_at: "2026-10-20"
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist()
        self.assertEqual(cm.exception.code, "PRIV_COMMAND_DENIED")

    def test_argv_forbidden_chars_denied(self):
        for value in ("bad\narg", "bad\x00arg", "bad;arg"):
            with self.subTest(value=value):
                with self._no_exec():
                    with self.assertRaises(priv.PrivilegeError) as cm:
                        priv.run("demo.run",
                                 ["bash", str(self.script), "-mode", value],
                                 reason="x")
                self.assertEqual(cm.exception.code, "PRIV_ARG_MISMATCH")

    def test_probe_direct(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="",
                                                         stderr="")) as run, \
                mock.patch.object(priv, "_no_new_privs", return_value=False):
            result = priv.probe()
        self.assertTrue(result.sudo_n)
        self.assertEqual(result.mechanism, "direct")
        self.assertTrue(result.context_sudo_ok)
        self.assertEqual(run.call_count, 2)

    def test_probe_systemd(self):
        calls = []
        def which(name, **kwargs):
            return "/usr/bin/sudo" if name == "sudo" else "/usr/bin/systemd-run"
        def fake_run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(priv.shutil, "which", side_effect=which), \
                mock.patch.object(priv.subprocess, "run",
                                  side_effect=fake_run), \
                mock.patch.object(priv, "_no_new_privs", return_value=True):
            result = priv.probe()
        self.assertFalse(result.sudo_n)
        self.assertEqual(result.mechanism, "systemd-run")
        self.assertTrue(result.usable)
        self.assertTrue(result.context_sudo_ok)
        self.assertTrue(any(call[0] == "/usr/bin/systemd-run" for call in calls))
        self.assertFalse(any(call[0] == "/usr/bin/sudo" for call in calls))

    def test_probe_nnp_without_systemd_unusable(self):
        with mock.patch.object(priv.shutil, "which", return_value=None), \
                mock.patch.object(priv, "_no_new_privs", return_value=True), \
                mock.patch.object(priv.subprocess, "run") as run:
            result = priv.probe()
        self.assertFalse(result.usable)
        self.assertEqual(result.mechanism, "none")
        run.assert_not_called()

    def test_probe_uses_safe_client_env(self):
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/evil.so"}), \
                mock.patch.object(priv.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="",
                                                         stderr="")) as run, \
                mock.patch.object(priv, "_no_new_privs", return_value=False):
            priv.probe()
        self.assertTrue(run.call_args_list)
        expected = pwd.getpwuid(os.getuid())
        for call in run.call_args_list:
            env = call.kwargs["env"]
            self.assertEqual(env["PATH"], priv._SAFE_PATH)
            self.assertEqual(env["HOME"], expected.pw_dir)
            self.assertEqual(env["USER"], expected.pw_name)
            self.assertEqual(env["PYTHONNOUSERSITE"], "1")
            self.assertNotIn("LD_PRELOAD", env)

    def test_run_non_root(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.mechanism, "direct")
        self.assertNotIn("sudo", execute.call_args.args[0])
        audit = json.loads(Path(result.audit_path).read_text(encoding="utf-8"))
        self.assertEqual(audit["entry_id"], "demo.run")
        self.assertEqual(audit["rc"], 0)
        self.assertEqual(audit["schema_version"], 1)
        self.assertFalse(audit["sudo_n_parent"])
        self.assertFalse(audit["sudo_n_context"])
        self.assertTrue(audit["run_id"])
        self.assertIsInstance(audit["duration_s"], float)
        self.assertNotIn("output_tail", audit)
        self.assertEqual(result.audit_file, result.audit_path)
        self.assertEqual(os.stat(result.audit_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(Path(result.audit_path).parent).st_mode
                         & 0o777, 0o700)

    def test_existing_audit_dir_mode_unchanged(self):
        log_dir = self.repo / "harness" / "log" / "privileged"
        log_dir.mkdir(parents=True)
        os.chmod(log_dir, 0o755)
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        self.assertEqual(os.stat(log_dir).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(result.audit_path).st_mode & 0o777, 0o600)

    def test_direct_env_uses_safe_whitelist(self):
        env = {"PATH": "/evil", "HOME": "/h", "DEMO": "d",
               "SECRET": "s", "LD_PRELOAD": "/evil.so"}
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test", env=env)
        run_env = execute.call_args.args[2]
        expected = pwd.getpwuid(os.getuid())
        self.assertEqual(run_env["PATH"], priv._SAFE_PATH)
        self.assertEqual(run_env["HOME"], expected.pw_dir)
        self.assertEqual(run_env["USER"], expected.pw_name)
        self.assertEqual(run_env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(run_env["DEMO"], "d")
        self.assertEqual(run_env["LC_PRIVILEGE_CONTEXT"], "1")
        self.assertNotIn("SECRET", run_env)
        self.assertNotIn("LD_PRELOAD", run_env)
        self.assertEqual(result.rc, 0)

    def test_home_injection_uses_trusted_identity(self):
        fake_home = Path(self.tmp.name) / "evilhome"
        fake_home.mkdir()
        (fake_home / "usercustomize.py").write_text(
            "print('malicious usercustomize')\n", encoding="utf-8")
        env = {"HOME": str(fake_home), "USER": "evil", "DEMO": "d"}
        expected = pwd.getpwuid(os.getuid())
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test", env=env)
        run_env = execute.call_args.args[2]
        self.assertEqual(run_env["HOME"], expected.pw_dir)
        self.assertNotEqual(run_env["HOME"], str(fake_home))
        self.assertEqual(run_env["USER"], expected.pw_name)
        self.assertEqual(run_env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(result.rc, 0)

    def test_aosp_root_redirect_denied(self):
        trusted = ("      AOSP_WS: /ws/aosp\n"
                   "      AOSP_ROOT: /ws/aosp\n"
                   "      ANDROID_PRODUCT_OUT: /ws/aosp/out")
        self.write_allowlist(
            env_passthrough="[HOME, AOSP_ROOT, AOSP_WS]",
            trusted_roots=trusted)
        env = {"AOSP_WS": "/tmp/evil", "AOSP_ROOT": "/tmp/evil"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(priv.shutil, "which",
                                  return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  side_effect=AssertionError("unexpected")):
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="x", env=env)
        self.assertEqual(cm.exception.code, "PRIV_ENV_PATH_DENIED")

    def test_aosp_paths_pass_validation(self):
        trusted = ("      AOSP_WS: /ws/aosp\n"
                   "      AOSP_ROOT: /ws/aosp\n"
                   "      ANDROID_PRODUCT_OUT: /ws/aosp/out")
        self.write_allowlist(
            env_passthrough=(
                "[HOME, AOSP_ROOT, AOSP_WS, ANDROID_PRODUCT_OUT]"),
            trusted_roots=trusted)
        env = {"AOSP_ROOT": "/ws/aosp",
               "AOSP_WS": "/ws/aosp",
               "ANDROID_PRODUCT_OUT": "/ws/aosp/out"}
        with mock.patch.object(priv.shutil, "which",
                               return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x", env=env)
        self.assertEqual(result.rc, 0)
        run_env = execute.call_args.args[2]
        self.assertEqual(run_env["AOSP_ROOT"], "/ws/aosp")
        self.assertEqual(run_env["ANDROID_PRODUCT_OUT"], "/ws/aosp/out")

    def test_relative_trusted_path_denied(self):
        self.write_allowlist(
            env_passthrough="[AOSP_ROOT]",
            trusted_roots="      AOSP_ROOT: /ws/aosp")
        env = {"AOSP_ROOT": "relative/aosp"}
        with mock.patch.object(priv.shutil, "which",
                               return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  side_effect=AssertionError("unexpected")):
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="x", env=env)
        self.assertEqual(cm.exception.code, "PRIV_ENV_PATH_DENIED")

    def test_controlled_env_without_trusted_roots_denied(self):
        self.write_allowlist(env_passthrough="[AOSP_ROOT]")
        env = {"AOSP_ROOT": "/ws/aosp"}
        with mock.patch.object(priv.shutil, "which",
                               return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  side_effect=AssertionError("unexpected")):
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="x", env=env)
        self.assertEqual(cm.exception.code, "PRIV_ENV_PATH_DENIED")

    def test_helper_binaries_resolve_with_safe_path(self):
        seen = []
        def which(name, path=None):
            seen.append(path)
            return f"/bin/{name}"
        with mock.patch.object(priv.shutil, "which", side_effect=which), \
                mock.patch.object(priv, "probe",
                                  return_value=priv.ProbeResult(
                                      True, False, True, "direct", True, "",
                                      True)), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")):
            self.write_allowlist(as_root="true")
            priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                     reason="test")
        self.assertTrue(seen)
        self.assertTrue(all(path == priv._SAFE_PATH for path in seen))

    def test_run_direct_root(self):
        self.write_allowlist(as_root="true")
        probe = priv.ProbeResult(True, False, True, "direct", True, "", True)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        self.assertEqual(execute.call_args.args[0][:3],
                         ["/bin/sudo", "-n", "--"])
        self.assertTrue(result.context_sudo_ok)

    def test_run_systemd_filters_env(self):
        self.write_allowlist(as_root="true")
        probe = priv.ProbeResult(False, True, True, "systemd-run", True, "", True)
        env = {"PATH": "/usr/bin", "HOME": "/h", "DEMO": "d",
               "SECRET": "s", "AOSP_WS": "/ws"}
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test", env=env)
        cmd = execute.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/systemd-run")
        self.assertIn("--property=NoNewPrivileges=no", cmd)
        self.assertIn("--setenv=DEMO=d", cmd)
        self.assertNotIn("--setenv=SECRET=s", cmd)
        self.assertIn("/bin/sudo", cmd)
        self.assertFalse(result.sudo_n)
        self.assertTrue(result.context_sudo_ok)
        client_env = execute.call_args.args[2]
        self.assertEqual(client_env["PATH"], priv._SAFE_PATH)
        self.assertEqual(client_env["LC_PRIVILEGE_CONTEXT"], "1")
        self.assertNotIn("SECRET", client_env)
        self.assertNotIn("AOSP_WS", client_env)

    def test_non_root_requires_privilege_uses_systemd(self):
        self.write_allowlist(as_root="false")
        self.allowlist.write_text(self.allowlist.read_text(encoding="utf-8")
                                  .replace("requires_privilege: false",
                                           "requires_privilege: true"),
                                  encoding="utf-8")
        probe = priv.ProbeResult(False, True, True, "systemd-run", True, "", True)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        cmd = execute.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/systemd-run")
        self.assertNotIn("/bin/sudo", cmd)
        self.assertEqual(result.mechanism, "systemd-run")

    def test_mechanism_forced_systemd_when_direct_usable(self):
        self.allowlist.write_text(self.allowlist.read_text(encoding="utf-8")
                                  .replace("mechanism: auto",
                                           "mechanism: systemd-run")
                                  .replace("requires_privilege: false",
                                           "requires_privilege: true"),
                                  encoding="utf-8")
        probe = priv.ProbeResult(True, False, True, "direct", True, "", True)
        with mock.patch.dict(os.environ,
                             {"LC_PRIVILEGE_MECHANISM": "direct"}), \
                mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")) as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        cmd = execute.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/systemd-run")
        self.assertEqual(result.mechanism, "systemd-run")
        self.assertFalse(hasattr(priv, "_ENV_MECHANISM"))

    def test_forced_systemd_requires_context_sudo(self):
        self.allowlist.write_text(self.allowlist.read_text(encoding="utf-8")
                                  .replace("mechanism: auto",
                                           "mechanism: systemd-run")
                                  .replace("requires_privilege: false",
                                           "requires_privilege: true"),
                                  encoding="utf-8")
        probe = priv.ProbeResult(True, False, True, "direct", True, "", False)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  side_effect=AssertionError("unexpected")):
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run",
                         ["bash", str(self.script), "-mode", "0"],
                         reason="test")
        self.assertEqual(cm.exception.code, "PRIV_SUDO_UNAVAILABLE")

    def test_no_probe_when_privilege_not_required(self):
        with mock.patch.object(priv, "probe") as probe_mock, \
                mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test")
        probe_mock.assert_not_called()
        self.assertEqual(result.rc, 0)

    def test_destructive_requires_confirm(self):
        self.write_allowlist(as_root="true", destructive="true")
        with self._no_exec():
            with self.assertRaises(priv.PrivilegeError) as cm:
                priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                         reason="x")
        self.assertEqual(cm.exception.code, "PRIV_DESTRUCTIVE_DENIED")

    def test_destructive_confirmed_executes(self):
        self.write_allowlist(as_root="true", destructive="true")
        probe = priv.ProbeResult(True, False, True, "direct", True, "", True)
        with mock.patch.dict(os.environ,
                             {"LC_PRIVILEGE_ALLOW_DESTRUCTIVE": "YES"}), \
                mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x", confirm_destructive=True)
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.mechanism, "direct")

    def test_timeout(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(124, "",
                                                "PRIV_TIMEOUT: 超过 1s")) \
                as execute:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x", timeout=1)
        self.assertEqual(result.rc, 124)
        self.assertIn("PRIV_TIMEOUT", result.error)
        self.assertEqual(execute.call_args.args[3], 1)

    def test_execute_timeout_kills_process_group(self):
        pid_file = Path(self.tmp.name) / "child.pid"
        command = ["/bin/sh", "-c",
                   f"sleep 30 & echo $! > {pid_file}; wait"]
        started = time.monotonic()
        rc, _, error = priv._execute(
            command, self.repo, {"PATH": priv._SAFE_PATH}, 0.3)
        elapsed = time.monotonic() - started
        self.assertEqual(rc, 124)
        self.assertIn("PRIV_TIMEOUT", error)
        self.assertLess(elapsed, 5)
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            except OSError:
                break
            if stat.split()[2] == "Z":
                break
            time.sleep(0.05)
        else:
            self.fail("后台子进程仍在运行，进程组未被回收")

    def test_execute_keyboard_interrupt_kills_group(self):
        proc = mock.Mock()
        proc.pid = 12345
        proc.communicate.side_effect = [KeyboardInterrupt, ("", "")]
        with mock.patch.object(priv.subprocess, "Popen", return_value=proc), \
                mock.patch.object(priv.os, "getpgid", return_value=12345), \
                mock.patch.object(priv.os, "killpg") as killpg:
            with self.assertRaises(KeyboardInterrupt):
                priv._execute(["x"], self.repo,
                              {"PATH": priv._SAFE_PATH}, 1)
        killpg.assert_called_once_with(12345, priv.signal.SIGKILL)

    def test_audit_failure_keeps_rc_and_empty_path(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(0, "ok", "")), \
                mock.patch.object(priv, "atomic_write_json",
                                  side_effect=OSError("disk")):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x")
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.audit_path, "")
        self.assertIn("PRIV_AUDIT_WRITE_FAILED", result.error)


    def test_cli_list_returns_0(self):
        self.assertEqual(priv._cli(["--list"]), 0)

    def test_cli_check_unusable_returns_3(self):
        probe = priv.ProbeResult(False, True, False, "none", False, "no", False)
        with mock.patch.object(priv, "probe", return_value=probe):
            rc = priv._cli(["--check"])
        self.assertEqual(rc, 3)

    def test_cli_command_failure_returns_1(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv, "_execute",
                                  return_value=(1, "boom", "")):
            rc = priv._cli([
                "--entry-id", "demo.run", "--reason", "x",
                "--", "bash", str(self.script), "-mode", "0"])
        self.assertEqual(rc, 1)

    def test_cli_unknown_entry_returns_2(self):
        with self._no_exec():
            rc = priv._cli([
                "--entry-id", "missing.run", "--reason", "x",
                "--", "bash", str(self.script), "-mode", "0"])
        self.assertEqual(rc, 2)

    def test_cli_mechanism_unavailable_returns_3(self):
        self.allowlist.write_text(self.allowlist.read_text(encoding="utf-8")
                                  .replace("requires_privilege: false",
                                           "requires_privilege: true"),
                                  encoding="utf-8")
        probe = priv.ProbeResult(False, True, False, "none", False, "no", False)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n, **kwargs: f"/bin/{n}"), \
                mock.patch.object(priv, "_execute",
                                  side_effect=AssertionError("unexpected")):
            rc = priv._cli([
                "--entry-id", "demo.run", "--reason", "x",
                "--", "bash", str(self.script), "-mode", "0"])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
