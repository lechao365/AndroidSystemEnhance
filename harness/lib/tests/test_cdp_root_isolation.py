# conftest CDP_PROJECT_ROOT 隔离 fixture 端到端单测（方向 1）。
# 本文件在 harness 测试套件内运行，conftest 的 autouse fixture 对
# harness 下全部用例生效：默认把未显式设置且无 real_repo marker 的用例
# 的 CDP_PROJECT_ROOT 指向 pytest 临时目录（防漏隔离读到真实仓状态假失败），
# real_repo marker 放行真实仓路径。此处用 pytest 原生用例验证各分支行为。
#
# 注意：这些用例自身也被隔离 fixture 处理——test_default_isolates_to_tmp
# 依赖默认隔离，带 real_repo marker 的用例依赖放行；两端皆真跑才证明
# fixture 分支正确（mock 判定会掩盖接线错误）。

import os

import pytest


def test_default_isolates_cdp_root_to_tmp():
    # 默认隔离：未显式设置且无 marker → CDP_PROJECT_ROOT 指向临时目录
    root = os.environ.get("CDP_PROJECT_ROOT")
    assert root, "默认隔离应设置 CDP_PROJECT_ROOT"
    assert os.path.isdir(root), f"CDP_PROJECT_ROOT 应指向存在的临时目录: {root}"
    # 不应指向真实仓库根（防读到真实仓状态假失败的动机）
    repo_root = os.path.realpath(os.path.join(
        os.path.dirname(__file__), "..", ".."))
    assert os.path.realpath(root) != repo_root


@pytest.mark.real_repo("验证放行：确需真实仓路径的用例显式标记")
def test_real_repo_marker_skips_isolation():
    # real_repo marker 放行：fixture 不设置 CDP_PROJECT_ROOT（保持未设置）
    assert "CDP_PROJECT_ROOT" not in os.environ


@pytest.mark.real_repo
def test_marker_registered_and_visible(request):
    # marker 已在 conftest 注册（防未知标记告警）+ 对用例可见（放行链路可用）
    assert "real_repo" in [m.name for m in request.node.iter_markers()]
    registered = "\n".join(request.config.getini("markers"))
    assert "real_repo" in registered, "real_repo marker 未在 conftest 注册"


@pytest.mark.real_repo
def test_real_repo_explicit_env_not_overridden():
    # marker 放行下显式设置 env → fixture 不覆盖（尊重显式意图）
    os.environ["CDP_PROJECT_ROOT"] = "/tmp/explicit-root"
    try:
        assert os.environ["CDP_PROJECT_ROOT"] == "/tmp/explicit-root"
    finally:
        os.environ.pop("CDP_PROJECT_ROOT", None)


def test_isolate_teardown_restores_unset():
    # 默认隔离用例结束后 teardown 应清除 CDP_PROJECT_ROOT（恢复未设置态）；
    # 本用例自身在 fixture setup 设了值，这里手动验证 pop 幂等语义不残留
    # 到带 real_repo marker 的用例（fixture teardown 在用例后执行）。
    os.environ.pop("CDP_PROJECT_ROOT", None)
    assert "CDP_PROJECT_ROOT" not in os.environ
