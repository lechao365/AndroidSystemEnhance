"""role_guard — 跨设备角色门禁（emit/apply 设备身份判定）

跨设备工作流中 emit 设备（远端）产批、apply 设备（本地）解析执行与推送，
专属命令不得跨设备误跑（apply 设备不得产批、emit 设备不得跑 apply 执行链）。
本模块按 HARNESS_ROLE 判定当前设备角色，供 CLI 入口在参数解析后、副作用
发生前接线 require_role(...)。

角色取值 emit | apply，读取优先级：
  1. 环境变量 HARNESS_ROLE（设备级属性，测试注入与设备声明）
  2. 缺省 apply（安全缺省：未配置机器视为 apply 设备，emit 专属命令被拦）

注：角色是设备身份而非仓库共享配置，故不经 paths.conf 分发（check_config
对 paths.conf 实行 key 白名单治理，HARNESS_ROLE 非路径语义不入白名单）。
_conf_role() 为预留兼容层：conf 层无值时静默落缺省，行为不变。
"""

import os
import sys
from pathlib import Path

# 仓根自举注入：本模块可能以短名（harness/lib 在 sys.path）被 import，
# 此时 harness.lib.paths 不可定位——与 cross-device 侧 cdp_paths 垫片同款
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_ROLE = "apply"


def _conf_role() -> str:
    """conf 层角色值（预留兼容层：读取失败或未配置返空串，交缺省处理）。"""
    try:
        from harness.lib.paths import env_path
        return env_path("HARNESS_ROLE", default="").strip()
    except Exception:
        return ""


def get_role() -> str:
    """当前设备角色：环境变量 HARNESS_ROLE 优先，conf 兼容层次之，缺省 apply。"""
    env = os.environ.get("HARNESS_ROLE", "").strip()
    if env:
        return env
    conf = _conf_role()
    return conf if conf else DEFAULT_ROLE


def require_role(expected: str) -> str:
    """角色门禁：当前角色与 expected 不符时打印中文错误（含 ROLE_MISMATCH
    分类字样与当前/期望角色）并 exit 1；匹配时返回当前角色。

    供 CLI 入口在参数解析后、副作用发生前调用（机器化角色防线）。
    """
    current = get_role()
    if current != expected:
        print(f"error: ROLE_MISMATCH 角色不匹配——本机角色为 {current}，"
              f"该命令仅限 {expected} 设备运行；请经环境变量 HARNESS_ROLE "
              f"声明本机角色（缺省 apply）", file=sys.stderr)
        sys.exit(1)
    return current
