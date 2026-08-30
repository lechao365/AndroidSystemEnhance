"""CDP 契约解析与校验（cross-device emit/apply 共用，仓内单份）。

格式（见 docs/cdp-contract.md，CDP-001 纪律：契约文档与解析器成对修改）：
  -s/-sv base:<12hex>
  意图: ...
  验收: ...   (-s 必须为「无」；-sv 必须非空且不得为「无」)
  方向: ...
退出码: 0 通过 / 3 参数错误·文件不可读或非 UTF-8 / 11 结构错误(含未知行) / 12 空批 / 14 三标签缺失
       / 15 base 非法 / 16 预算超限(>500 或 <50) / 17 验收规则违规 / 18 base 不匹配
角色差异: validate_batch 恒返回原始判定码；降级（apply 仅对 17 → WARN）由
main() 依据 SOFT_ERRORS + role 统一处理（16 双角色 blocking）。
"""
import hashlib
import re
import sys
from dataclasses import dataclass

MIN_CHARS = 50
MAX_CHARS = 500
BASE_RE = re.compile(r"^[0-9a-fA-F]{12}$")
# 首行结构只约束「模式标记 + base: 字样」，base 值合法性交给 BASE_RE（保 15 可达）
MODE_RE = re.compile(r"^(-s|-sv)\s+base:\s*(\S+)\s*$")
TAG_RE = re.compile(r"^(意图|验收|方向):\s*(.*)$")

EXIT_OK = 0
EXIT_ARGS = 3
EXIT_STRUCT = 11
EXIT_EMPTY = 12
EXIT_NO_CONTRACT = 14
EXIT_BAD_BASE = 15
EXIT_BUDGET = 16
EXIT_ACCEPTANCE = 17
EXIT_BASE_MISMATCH = 18

# 仅 17 在 apply 角色降级（spec §4.3）；16 不降级
SOFT_ERRORS = {EXIT_ACCEPTANCE}


@dataclass
class Batch:
    mode: str = ""        # "s" | "sv"
    base: str = ""
    intent: str = ""
    acceptance: str = ""
    direction: str = ""
    text: str = ""


def normalize_batch_text(text: str) -> str:
    """剥 BOM、逐行 strip、去空行、统一 LF、折叠行内连续空白为单空格。
    batch_id 与解析共用；折叠保证 batch_id 不因传输多空格而漂移。"""
    text = text.lstrip("\ufeff")
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def batch_id_from_text(text: str) -> str:
    norm = normalize_batch_text(text)
    # 逐行删净行内空白再哈希：折叠只归一连续空白，插入单空格仍会漂移 batch_id；
    # 删净后 batch_id 仅依赖文字内容，抗任意空白插入/重排（normalize_batch_text 不动）
    stripped = "\n".join(re.sub(r"\s+", "", ln) for ln in norm.splitlines())
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12]


def parse_batch(text: str) -> Batch:
    b = Batch(text=text)
    norm = normalize_batch_text(text)
    if not norm:
        return b
    lines = norm.splitlines()
    m = MODE_RE.match(lines[0])
    if m:
        b.mode = m.group(1)[1:]  # "-sv" -> "sv", "-s" -> "s"
        b.base = m.group(2).lower()
        for ln in lines[1:]:
            t = TAG_RE.match(ln)
            if t:
                key, val = t.group(1), t.group(2).strip()
                if key == "意图":
                    b.intent = val
                elif key == "验收":
                    b.acceptance = val
                elif key == "方向":
                    b.direction = val
    return b


def validate_batch(text: str, role: str = "emit"):
    """返回 (exit_code, errors)。恒返回原始判定码，降级在 main()。"""
    norm = normalize_batch_text(text)
    if not norm:
        return EXIT_EMPTY, ["空批次"]

    lines = norm.splitlines()
    if not MODE_RE.match(lines[0]):
        return EXIT_STRUCT, [f"首行必须为 -s/-sv base:<12hex>，实际: {lines[0]!r}"]
    seen_tags: dict[str, int] = {}
    for i, ln in enumerate(lines[1:], start=2):
        t = TAG_RE.match(ln)
        if not t:
            return EXIT_STRUCT, [f"未知行（须为 意图/验收/方向: 前缀）: {ln!r}"]
        if t.group(1) in seen_tags:
            return EXIT_STRUCT, [
                f"重复标签 {t.group(1)}（行 {seen_tags[t.group(1)]} 与行 {i}），"
                "三标签各占一段且不得重复",
            ]
        seen_tags[t.group(1)] = i

    b = parse_batch(norm)
    if not (b.intent and b.acceptance and b.direction):
        return EXIT_NO_CONTRACT, ["意图/验收/方向 三标签必填"]

    if not BASE_RE.match(b.base):
        return EXIT_BAD_BASE, [f"base 必须为 12 位 hex: {b.base!r}"]

    n = len(norm)
    if not (MIN_CHARS <= n <= MAX_CHARS):
        return EXIT_BUDGET, [f"预算 {MIN_CHARS}~{MAX_CHARS} 字符，实际 {n}"]

    if b.mode == "sv":
        if not b.acceptance or b.acceptance == "无":
            return EXIT_ACCEPTANCE, ["-sv 批次验收必须非空且不得为「无」"]
    else:
        if b.acceptance != "无":
            return EXIT_ACCEPTANCE, ["-s 批次验收必须为「无」"]

    return EXIT_OK, []


def base_matches(text: str, expect_head12: str) -> bool:
    """批次 base 是否与 apply 侧起始 HEAD（前 12 位）匹配（忽略大小写）。"""
    b = parse_batch(text)
    return bool(b.base) and bool(expect_head12) and \
        b.base.lower() == expect_head12.strip().lower()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: cdp_parse.py --role emit|apply [--expect-base <12hex>] <批次文件>")
        return 0
    # 手工解析参数：缺失参数统一 exit 3（argparse 默认 exit 2，不符合契约表）
    role, expect, path = "emit", None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--role" and i + 1 < len(argv):
            role = argv[i + 1]; i += 2; continue
        if a == "--expect-base" and i + 1 < len(argv):
            expect = argv[i + 1]; i += 2; continue
        if a.startswith("--"):
            print(f"error: 未知参数 {a}")
            return EXIT_ARGS
        if path is None:
            path = a; i += 1; continue
        print(f"error: 多余参数 {a}")
        return EXIT_ARGS
    if role not in ("emit", "apply") or path is None:
        print("error: 用法: cdp_parse.py --role emit|apply [--expect-base <12hex>] <批次文件>")
        return EXIT_ARGS
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"error: 批次文件不可读或非 UTF-8: {e}")
        return EXIT_ARGS

    code, errs = validate_batch(text, role=role)
    softened = code in SOFT_ERRORS and role == "apply"
    for e in errs:
        print(f"{'warn' if softened else 'error'}: {e}")
    if code != EXIT_OK and not softened:
        # 失败路径不打印 batch_id/mode（空批会打印空串误导上层）
        return code
    if expect is not None and not base_matches(text, expect):
        b = parse_batch(text)
        print(f"error: base 不匹配（批次 {b.base} != 本地 HEAD {expect.strip()}），整批拒绝")
        return EXIT_BASE_MISMATCH
    b = parse_batch(text)
    print(f"batch_id: {batch_id_from_text(text)}")
    print(f"mode: {b.mode} base: {b.base}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())