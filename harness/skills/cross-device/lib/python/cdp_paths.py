"""兼容垫片（批次四 A5）：cdp_paths 实现已上移 harness/lib/cdp_paths.py。

本文件占住 cdp_paths 模块名：cdp_timing/cdp_receipt/cdp_parse 等 CLI 与
测试以 sys.path 同目录方式 `import cdp_paths`，经本垫片 re-export 主实现
全部公开符号，行为不变。新代码请直接
`from harness.lib.cdp_paths import ...`（或经 harness.lib 包引用）。

cdp_parse_script（cross-device 协议专属路径）依赖本包物理位置，保留于此
不随主实现上移。
"""
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from harness.lib.cdp_paths import (  # noqa: E402,F401
    atomic_write_text,
    data_baselines_dir,
    data_known_issues_dir,
    data_verify_results_dir,
    log_apply_dir,
    project_root,
)


def cdp_parse_script() -> _Path:
    """cdp_parse.py 路径（cross-device 协议专属，锚定本包目录）。"""
    return _Path(__file__).resolve().parent / "cdp_parse.py"
