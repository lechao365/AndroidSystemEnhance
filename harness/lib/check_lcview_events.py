#!/usr/bin/env python3
# ============================================================
# check_lcview_events.py — lcview 事件 schema 与内核发射点契约一致性 gate
# 设计目的：AOSP lcview_events.json（config/）定义事件 id 与字段类型序，
#   内核发射点（LcIod lciod_usbd-stats.c / lciod_usbd.c 的 lcview_builder_*
#   调用序）是实际落盘数据的真相源。历史上依赖人工对照评审，schema 与
#   发射点字段类型/顺序单侧漂移时，用户态按 schema 解析二进制记录会错位
#   （如 int64 字段被当 string 解析、字段序错配），静态编译不报错。
#   本脚本比对两侧：
#     - 事件 id：schema id 必须与内核 lcview_events.h 的 LCVIEW_EVENT_* 宏
#       值一致（同一 name）
#     - 字段类型序：schema fields[].type 序必须与内核发射点 add_* 调用序
#       一致（add_int→int64、add_str→string、add_int32→int32、
#       add_float→float、add_binary→binary）
# 用法：python3 harness/lib/check_lcview_events.py [--repo <code 仓根>]
# 退出码：0 契约一致 / 1 漂移 / 2 文件缺失
# ============================================================

import argparse
import re
import sys
from pathlib import Path

# 相对 code 仓根的 schema 与内核源文件
SCHEMA_REL = ("rpi5/aosp/new/vendor/lechao/services/lechao_lcview/config/"
              "lcview_events.json")
EVENTS_H_REL = ("rpi5/kernel/new/vendor/lechao/LcView/lcview_events.h")
# 内核发射点源文件（lcview_builder_* 调用点所在，可能随模块扩展新增）
EMIT_SOURCE_RELS = [
    "rpi5/kernel/new/vendor/lechao/LcIod/lciod_usbd-stats.c",
    "rpi5/kernel/new/vendor/lechao/LcIod/lciod_usbd.c",
]

# 内核发射点字段类型映射（add_* API → schema 字段类型）；正则捕获的是
# API 去掉 "add_" 前缀后的余段（如 add_int → int）
_ADD_TYPE = {
    "int": "int64",
    "int32": "int32",
    "str": "string",
    "float": "float",
    "binary": "binary",
}

# 事件 id 宏：`#define LCVIEW_EVENT_XXX  N`（数字后可跟随任意注释文本，
# 注释可能跨行 `/* ...` 不在本行闭合，故数字后仅要求行尾任意内容；
# finditer 须用 re.M 令 ^/$ 按行锚定）
_EVENT_ID_RE = re.compile(
    r"^#define\s+(LCVIEW_EVENT_\w+)\s+(\d+)\b.*$", re.M)
# 发射点调用：`b = lcview_builder_start(LCVIEW_EVENT_X, LEVEL)`
_START_RE = re.compile(r"lcview_builder_start\(\s*(LCVIEW_EVENT_\w+)\s*,")
# 字段追加调用：`lcview_builder_add_int(b, ...)` / add_str 等
_ADD_RE = re.compile(r"lcview_builder_add_(\w+)\(")
# 提交/取消边界：commit 之后为下一发射点（cancel 属丢弃路径不入字段序）
_COMMIT_RE = re.compile(r"lcview_builder_commit\(")


def parse_schema(text: str) -> list[dict]:
    """解析 lcview_events.json → [{id, name, fields:[type...]}]；非法结构抛 ValueError。"""
    import json
    data = json.loads(text)
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("schema 缺 events 列表")
    out = []
    for ev in events:
        if not isinstance(ev.get("id"), int) or not isinstance(ev.get("name"), str):
            raise ValueError(f"事件缺 id/name: {ev}")
        fields = ev.get("fields", [])
        if not isinstance(fields, list):
            raise ValueError(f"事件 {ev['name']} fields 非列表")
        types = [f.get("type") for f in fields]
        if any(not isinstance(t, str) for t in types):
            raise ValueError(f"事件 {ev['name']} 存在无类型字段")
        out.append({"id": ev["id"], "name": ev["name"], "fields": types})
    return out


def parse_event_id_macros(text: str) -> dict[str, int]:
    """解析 lcview_events.h → {宏名: id}。"""
    out = {}
    for m in _EVENT_ID_RE.finditer(text):
        out[m.group(1)] = int(m.group(2))
    return out


def parse_emit_sequences(text: str) -> dict[str, list[str]]:
    """扫描内核发射点 → {事件宏名: 字段类型序}。

    对每个 lcview_builder_start(LCVIEW_EVENT_X, ...) 调用，取其位置到下一
    start（或文件尾）之间的 add_* 调用序，映射为 schema 字段类型。commit
    前的 add 序列即该事件的字段序（cancel 属丢弃路径，不影响 schema 契约）。
    """
    out = {}
    starts = list(_START_RE.finditer(text))
    for i, sm in enumerate(starts):
        name = sm.group(1)
        seg_end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        seg = text[sm.end():seg_end]
        # 该事件发射点的 add 序列：取到 commit（字段追加在 commit 前）
        add_types = []
        for am in _ADD_RE.finditer(seg):
            api = am.group(1)
            if api not in _ADD_TYPE:
                continue  # 非字段追加 API（如 add 失败路径无关）
            add_types.append(_ADD_TYPE[api])
        if add_types:
            out[name] = add_types
    return out


def compare(repo: Path) -> tuple[int, str]:
    """执行比对，返回 (rc, 描述)。"""
    schema_path = repo / SCHEMA_REL
    events_h = repo / EVENTS_H_REL
    if not schema_path.is_file() or not events_h.is_file():
        missing = []
        for p, rel in ((schema_path, SCHEMA_REL), (events_h, EVENTS_H_REL)):
            if not p.is_file():
                missing.append(rel)
        return 2, f"文件缺失: {', '.join(missing)}"
    try:
        schema = parse_schema(schema_path.read_text(encoding="utf-8"))
    except ValueError as e:
        return 1, f"schema 解析异常: {e}"

    macros = parse_event_id_macros(events_h.read_text(encoding="utf-8"))
    emit = {}
    for rel in EMIT_SOURCE_RELS:
        p = repo / rel
        if not p.is_file():
            return 2, f"文件缺失: {rel}"
        emit.update(parse_emit_sequences(p.read_text(encoding="utf-8")))

    problems = []
    # name → 宏名：LCVIEW_EVENT_ + name.upper()（schema name 与宏后缀一致契约）
    name_to_macro = {}
    for ev in schema:
        macro = "LCVIEW_EVENT_" + ev["name"].upper()
        name_to_macro[ev["name"]] = macro
        # 1) id 契约：schema id 与内核宏 id 一致
        if macro not in macros:
            problems.append(f"事件 {ev['name']} id={ev['id']}: 内核宏 {macro} 未定义")
            continue
        if macros[macro] != ev["id"]:
            problems.append(f"事件 {ev['name']}: schema id={ev['id']} != 内核宏 "
                            f"{macro}={macros[macro]}")
        # 2) 字段类型序契约：schema 序 vs 内核发射点序
        emit_fields = emit.get(macro)
        if emit_fields is None:
            problems.append(f"事件 {ev['name']}: 内核无 {macro} 发射点（或字段序为空）")
            continue
        if emit_fields != ev["fields"]:
            problems.append(f"事件 {ev['name']} 字段类型序漂移: "
                            f"schema={ev['fields']} 内核发射点={emit_fields}")
    if problems:
        return 1, "\n".join(problems)
    return 0, f"一致: {len(schema)} 事件 id 与字段类型序全部匹配"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2] / "code"),
                    help="code 仓根（默认脚本相对推断）")
    args = ap.parse_args()
    repo = Path(args.repo)
    rc, msg = compare(repo)
    print(f"[{'OK' if rc == 0 else '漂移' if rc == 1 else '缺失'}] {msg}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
