"""verify_common — workspace-verify 跨脚本共享基础（批次四 A3/D 收敛）。

统一原子写 JSON 原语：此前各验证脚本各自复制 _atomic_write_json（6 份
同构），本模块收敛为单一实现，各脚本保留原函数名作薄壳委托（mock 点/
签名不变）。

注意：本模块与 cdp_paths 同目录（harness/lib）。
"""
import json
import os
import threading
from pathlib import Path


def atomic_write_json(path, data):
    """原子写 JSON 产物（统一原语）：tmp 带 pid + 线程 id 防并发互写
    （lib-13 与 cdp_paths.atomic_write_text 口径统一：同 pid 多线程并发
    写同路径此前互写）+ os.replace，防半截文件被当证据。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(
        f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
