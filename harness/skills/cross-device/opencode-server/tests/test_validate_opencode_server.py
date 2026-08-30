"""validate_opencode_server 单测：迁移缺陷护栏（||true 吞错 / EnvironmentFile 硬编码 / 非原子写 / --help 副作用 / LcSkills core 残留引用）。

构造 GOOD 合规脚本片段，逐项注入违规样例验证被拦截。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parent.parent / "validate_opencode_server.py"

spec = importlib.util.spec_from_file_location("validate_opencode_server", VALIDATOR)
vmod = importlib.util.module_from_spec(spec)
sys.modules["validate_opencode_server"] = vmod
spec.loader.exec_module(vmod)


GOOD = """#!/bin/bash
set -uo pipefail
-h|--help)
    usage
    exit 0
    ;;
lc_init "start_opencode_server"
EnvironmentFile=$SERVER_ENV_FILE
tmp_file="$SYSTEMD_USER_DIR/.${SERVICE_UNIT}.tmp"
mv -f "$tmp_file" "$SERVICE_FILE"
"""


@pytest.fixture
def fake_script(tmp_path, monkeypatch):
    """把 validator 的 SCRIPT 指向 tmp 下的脚本文件，返回写内容函数。"""
    f = tmp_path / "start-opencode-server.sh"
    monkeypatch.setattr(vmod, "SCRIPT", f)

    def _write(text: str) -> Path:
        f.write_text(text, encoding="utf-8")
        return f

    return _write


class TestNoTrueSwallow:
    def test_clean_passes(self, fake_script):
        fake_script(GOOD)
        assert vmod.validate_no_true_swallow() == []

    def test_or_true_flagged(self, fake_script):
        fake_script(GOOD + 'pgrep -f "opencode web" || true\n')
        errors = vmod.validate_no_true_swallow()
        assert len(errors) == 1
        assert "|| true" in errors[0]

    def test_or_block_not_flagged(self, fake_script):
        fake_script(GOOD + 'cmd || {\n    log_warn "fail"\n}\n')
        assert vmod.validate_no_true_swallow() == []


class TestEnvFile:
    def test_clean_passes(self, fake_script):
        fake_script(GOOD)
        assert vmod.validate_env_file() == []

    def test_hardcoded_home_flagged(self, fake_script):
        fake_script(GOOD + "EnvironmentFile=%h/.config/opencode/server.env\n")
        errors = vmod.validate_env_file()
        assert any("硬编码" in e for e in errors)

    def test_hardcoded_home_var_flagged(self, fake_script):
        fake_script(GOOD.replace("$SERVER_ENV_FILE", "$HOME/.config/opencode/server.env"))
        errors = vmod.validate_env_file()
        assert any("硬编码" in e for e in errors)

    def test_missing_env_var_flagged(self, fake_script):
        fake_script(GOOD.replace("EnvironmentFile=$SERVER_ENV_FILE", "EnvironmentFile=/tmp/x.env"))
        errors = vmod.validate_env_file()
        assert any("未引用 $SERVER_ENV_FILE" in e for e in errors)


class TestAtomicWrite:
    def test_clean_passes(self, fake_script):
        fake_script(GOOD)
        assert vmod.validate_atomic_write() == []

    def test_missing_tmp_pattern_flagged(self, fake_script):
        fake_script(GOOD.replace('tmp_file="$SYSTEMD_USER_DIR/.${SERVICE_UNIT}.tmp"', 'tmp_file="/tmp/x"'))
        errors = vmod.validate_atomic_write()
        assert any("同目录临时文件" in e for e in errors)

    def test_missing_mv_flagged(self, fake_script):
        fake_script(GOOD.replace('mv -f "$tmp_file" "$SERVICE_FILE"', 'cat > "$SERVICE_FILE"'))
        errors = vmod.validate_atomic_write()
        assert any("原子替换" in e for e in errors)


class TestHelpBeforeInit:
    def test_clean_passes(self, fake_script):
        fake_script(GOOD)
        assert vmod.validate_help_before_init() == []

    def test_help_after_init_flagged(self, fake_script):
        fake_script(
            "#!/bin/bash\nset -uo pipefail\n"
            'lc_init "start_opencode_server"\n'
            "-h|--help)\n    usage\n    exit 0\n    ;;\n"
            "EnvironmentFile=$SERVER_ENV_FILE\n"
        )
        errors = vmod.validate_help_before_init()
        assert len(errors) == 1
        assert "副作用" in errors[0]

    def test_no_help_branch_flagged(self, fake_script):
        fake_script(GOOD.replace("-h|--help)\n    usage\n    exit 0\n    ;;\n", ""))
        errors = vmod.validate_help_before_init()
        assert any("未发现 --help 拦截分支" in e for e in errors)

    def test_no_init_flagged(self, fake_script):
        fake_script(GOOD.replace('lc_init "start_opencode_server"\n', ""))
        errors = vmod.validate_help_before_init()
        assert any("未发现 lc_init 调用" in e for e in errors)


class TestNoLcSkillsCoreRef:
    def test_clean_passes(self, fake_script):
        fake_script(GOOD)
        assert vmod.validate_no_lcskills_core_ref() == []

    def test_core_scripts_ref_flagged(self, fake_script):
        fake_script(GOOD + "bash core/scripts/start-opencode-server.sh\n")
        errors = vmod.validate_no_lcskills_core_ref()
        assert len(errors) == 1
        assert "core/scripts/" in errors[0]

    def test_lc_bootstrap_ref_flagged(self, fake_script):
        fake_script(GOOD + "source core/lib/shell/lc_bootstrap.sh\n")
        errors = vmod.validate_no_lcskills_core_ref()
        assert len(errors) == 1
        assert "lc_bootstrap" in errors[0]


class TestFilesExist:
    def test_missing_script_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vmod, "SCRIPT", tmp_path / "nope.sh")
        monkeypatch.setattr(vmod, "SKILL_MD", tmp_path / "SKILL.md")
        errors = vmod.validate_files_exist()
        assert len(errors) == 2
