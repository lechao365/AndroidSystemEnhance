"""verify_common — workspace-verify 跨脚本共享基础（批次四 A3/D 收敛）。

此前各验证脚本各自复制 _atomic_write_json（6 份同构）与 batch_id 三级
回落（4 份口径漂移：ws_package 字典序取首 vs ws_acceptance 唯一文件级
vs cdp_timing current-batch 指针），漂移后同批产物散到不同 batch id。
本模块收敛为单一实现，各脚本保留原函数名作薄壳委托（mock 点/签名不变）。

注意：本模块与 cdp_paths 同目录（harness/lib）；对 cross-device 公开 API
（cdp_timing.resolve_batch_id）经 sys.path 注入延迟 import，失败静默降级
（打点/产物命名属诊断面，不得阻断验证主流程）。
"""
import json
import os
import sys
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent
_CDP_LIB = _LIB_DIR.parent / "skills" / "cross-device" / "lib" / "python"


def atomic_write_json(path, data):
    """原子写 JSON 产物（统一原语）：tmp 带 pid 防并发互写 + os.replace，
    防半截文件被当证据（对齐 cdp_paths.atomic_write_text 惯例）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def resolve_batch_id_fallback(explicit=None):
    """batch_id 四级回落（统一口径，B6）：
    显式参 > CDP_BATCH_ID 环境变量 > current-batch.json 指针 >
    log 目录唯一 timings 文件（多文件返 None 防误标其他批次）。

    返回 batch_id 或 None；任何一级来源异常（env 值非法/指针损坏/glob
    OSError）按未提供处理继续回落。
    """
    if explicit:
        return explicit
    env_id = os.environ.get("CDP_BATCH_ID", "").strip()
    if env_id:
        return env_id
    if str(_CDP_LIB) not in sys.path:
        sys.path.insert(0, str(_CDP_LIB))
    try:
        import cdp_timing
        bid = cdp_timing.resolve_batch_id()  # env > current-batch.json
        if bid:
            return bid
    except Exception:
        pass
    try:
        import cdp_paths
        files = sorted(cdp_paths.log_apply_dir().glob("timings-*.json"))
        if len(files) == 1:
            return files[0].stem[len("timings-"):]
    except Exception:
        pass
    return None
