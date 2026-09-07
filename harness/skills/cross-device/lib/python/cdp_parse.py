"""CDP 契约解析与校验（cross-device emit/apply 共用，仓内单份）。

格式（见 docs/cdp-contract.md，CDP-001 纪律：契约文档与解析器成对修改）：
  -s/-sv base:<12hex>
  checksum: <16hex>   （可选元数据行，须紧跟首行；值 = 首行（mode+base）+
                      正文（checksum 行以下全部行）规范化后 sha256 前 16 位，
                      首行纳入覆盖防模式/base 篡改；emit 产批经 --gen-checksum
                      生成，apply 侧存在即校验，缺失为旧格式 warn 兼容）
  意图: ...
  验收: ...   (-s 必须为「无」；-sv 必须非空且不得为「无」)
  方向: ...
退出码: 0 通过 / 1 checksum 不符(篡改/损坏) / 3 参数错误·文件不可读或非 UTF-8
        / 11 结构错误(含未知行) / 12 空批 / 14 三标签缺失
        / 15 base 非法 / 16 预算超限(>500 或 <50) / 17 验收规则违规 / 18 base 不匹配
角色差异: validate_batch 恒返回原始判定码；降级（apply 仅对 17 → WARN）由
main() 依据 SOFT_ERRORS + role 统一处理（16/18 双角色 blocking；19 引号
违规仅 emit 角色校验；1 checksum 不符双角色 blocking——防传输篡改）。
"""
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# 仓根自举注入（角色门禁依赖 harness/lib）：cdp_parse 以 CLI 直跑为主，
# sys.path[0] 为脚本目录——与 cdp_paths 垫片同款自举
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness.lib.role_guard import require_role  # noqa: E402

MIN_CHARS = 50
MAX_CHARS = 500
BASE_RE = re.compile(r"^[0-9a-fA-F]{12}$")
# 首行结构只约束「模式标记 + base: 字样」，base 值合法性交给 BASE_RE（保 15 可达）
MODE_RE = re.compile(r"^(-s|-sv)\s+base:\s*(\S+)\s*$")
TAG_RE = re.compile(r"^(意图|验收|方向):\s*(.*)$")
# 验收 case id：限小写字母数字与连字符（方向 1 契约）
CASE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# checksum 行（批次防篡改）：紧跟首行的元数据行，值 = 首行（mode+base）+
# 正文（checksum 行以下全部行）规范化后 sha256 前 16 位（首行纳入覆盖，
# 防 -sv→-s 等模式/base 篡改静默过 checksum）
CHECKSUM_RE = re.compile(r"^checksum:\s*([0-9a-fA-F]{16})$")

EXIT_OK = 0
EXIT_CHECKSUM = 1
EXIT_ARGS = 3
EXIT_STRUCT = 11
EXIT_EMPTY = 12
EXIT_NO_CONTRACT = 14
EXIT_BAD_BASE = 15
EXIT_BUDGET = 16
EXIT_ACCEPTANCE = 17
EXIT_BASE_MISMATCH = 18
EXIT_QUOTE = 19

# 仅 17 在 apply 角色降级（spec §4.3）；16/1 不降级
SOFT_ERRORS = {EXIT_ACCEPTANCE}


@dataclass
class Batch:
    mode: str = ""        # "s" | "sv"
    base: str = ""
    intent: str = ""
    acceptance: str = ""
    direction: str = ""
    text: str = ""
    checksum: str = ""    # 头部声明的正文 checksum（无则空串，旧格式）


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


def _split_checksum(norm: str) -> tuple[str, str, str]:
    """规范化批次文本 → (声明的 checksum, checksum 覆盖文本, 正文文本)。

    checksum 行仅认「紧跟首行」的头部位置；出现在他处不在此提取，由
    validate_batch 的标签循环按未知行报 11 结构错误。覆盖文本 = 首行
    （mode+base）+ 正文（首行与 checksum 行以下全部行）——首行纳入
    checksum 覆盖，防 -sv→-s 篡改经 exit 17 软错降级静默放行（CDP-02）。
    """
    lines = norm.splitlines()
    body = lines[1:]
    claimed = ""
    if body:
        m = CHECKSUM_RE.match(body[0])
        if m:
            claimed = m.group(1).lower()
            body = body[1:]
    covered = lines[0] + "\n" + "\n".join(body) if lines else ""
    return claimed, covered, "\n".join(body)


def batch_checksum(covered: str) -> str:
    """checksum 覆盖范围（首行 + 正文）的校验值：规范化后 sha256 前 16 位。

    与 batch_id 同源归一（剥 BOM/strip/去空行/折叠空白/LF），emit/apply
    两侧对同一覆盖文本恒得同值，抗传输层空白漂移。
    """
    return hashlib.sha256(
        normalize_batch_text(covered).encode("utf-8")).hexdigest()[:16]


def with_checksum(text: str) -> str:
    """emit 侧产批收尾：在首行后插入/刷新 checksum: <16hex> 行。

    先规范化再定位首行（与解析口径对称，CDP-01：原始文本首行前有空行时
    按原文 splitlines 定位会把 checksum 行插错位，apply 侧恒拒 exit 11）；
    checksum 覆盖首行（mode+base）+ 正文（checksum 行以下全部行，CDP-02：
    防 -sv→-s 篡改静默降级放行）；对既有 checksum 行原位重算（批次编辑后
    刷新）；输出为规范化文本（空行/BOM 已剥）。
    """
    norm = normalize_batch_text(text)
    lines = norm.splitlines()
    if not lines:
        return norm
    first, body = lines[0], lines[1:]
    if body and CHECKSUM_RE.match(body[0].strip()):
        body = body[1:]
    covered = first + "\n" + "\n".join(body)
    fresh = f"checksum: {batch_checksum(covered)}"
    return "\n".join([first, fresh, *body]) + "\n"


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
        b.checksum, _, body = _split_checksum(norm)
        for ln in body.splitlines():
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
    # checksum 行（紧跟首行的元数据行）：存在即校验（对首行+正文重算比对，
    # 双角色 blocking——篡改/损坏整批拒绝）；他处出现按未知行报 11（下
    # 方标签循环）。缺失为旧格式批次，main 侧 warn 兼容放行
    claimed, covered, body_norm = _split_checksum(norm)
    if claimed and claimed != batch_checksum(covered):
        return EXIT_CHECKSUM, [
            f"CHECKSUM_MISMATCH 批次（首行+正文）与头部 checksum 不符（头部 {claimed}，"
            f"重算 {batch_checksum(covered)}），疑似传输篡改或损坏，"
            "整批拒绝"
        ]
    seen_tags: dict[str, int] = {}
    for i, ln in enumerate(body_norm.splitlines(),
                           start=3 if claimed else 2):
        t = TAG_RE.match(ln)
        if not t:
            # 行号基于规范化后文本（去空行/折叠空白），与原始批次文件行号
            # 可能不一致——消息注明口径，排障时对照规范化行序（CDP-11）
            return EXIT_STRUCT, [
                f"未知行（须为 意图/验收/方向: 前缀，规范化后行号 {i}）: {ln!r}"
            ]
        if t.group(1) in seen_tags:
            return EXIT_STRUCT, [
                f"重复标签 {t.group(1)}（规范化后行 {seen_tags[t.group(1)]} "
                f"与行 {i}，行号为规范化后行号），三标签各占一段且不得重复",
            ]
        seen_tags[t.group(1)] = i

    b = parse_batch(norm)
    if not (b.intent and b.acceptance and b.direction):
        return EXIT_NO_CONTRACT, ["意图/验收/方向 三标签必填"]

    if not BASE_RE.match(b.base):
        return EXIT_BAD_BASE, [f"base 必须为 12 位 hex: {b.base!r}"]

    # 预算 = 首行 + 正文（checksum 行为机器元数据不计入，防 500 上限被
    # 元数据挤占致批次被 16 误拒；无 checksum 行时与旧口径 len(norm) 等值）
    n = len(lines[0]) + 1 + len(body_norm)
    if not (MIN_CHARS <= n <= MAX_CHARS):
        return EXIT_BUDGET, [f"预算 {MIN_CHARS}~{MAX_CHARS} 字符，实际 {n}"]

    # 批次六 C1 引号防呆：批次正文禁单双引号（' 与 "）——apply 侧写
    # 临时文件方式不受 emit 控制，正文含引号会被 shell 展开吞字致批次
    # 结构损坏（收据 batch_base 空根因）。仅 emit 角色拒（19）：批次
    # 尚未交付，修正成本为零；apply 角色不拒（文本已产生，拒批只断链
    # 不修复，残留引号由既有 heredoc 写入法兜底）。
    if role == "emit" and ("'" in norm or '"' in norm):
        return EXIT_QUOTE, ["批次正文含单/双引号字符（' 或 \"）——"
                            "传输层会展开吞字，须改用中文标点或去引号"]

    if b.mode == "sv":
        if not b.acceptance or b.acceptance == "无":
            return EXIT_ACCEPTANCE, ["-sv 批次验收必须非空且不得为「无」"]
        # 方向 2：-sv 验收行校验 case/manual 契约（违规返 17，contract 与
        # 解析器同批一致）
        err = check_acceptance_syntax(b.acceptance)
        if err:
            return EXIT_ACCEPTANCE, [err]
    else:
        if b.acceptance != "无":
            return EXIT_ACCEPTANCE, ["-s 批次验收必须为「无」"]

    return EXIT_OK, []


def base_matches(text: str, expect_head12: str) -> bool:
    """批次 base 是否与 apply 侧起始 HEAD（前 12 位）匹配（忽略大小写）。"""
    b = parse_batch(text)
    return bool(b.base) and bool(expect_head12) and \
        b.base.lower() == expect_head12.strip().lower()


def check_acceptance_syntax(acc: str) -> str | None:
    """-sv 验收语法校验（CDP-001 契约，见 docs/cdp-contract.md 验收语法行）：
    case:<id>[,<id>...]（id 限小写字母数字与连字符，多个用逗号分隔）或
    manual:<自由文本>（仅 manual 模式保留自由文本）。违规返回错误消息
    （调用方转 EXIT_ACCEPTANCE=17），合法返回 None。"""
    if acc == "无":
        return None
    if acc.startswith("case:"):
        ids = [i.strip() for i in acc[len("case:"):].split(",") if i.strip()]
        if not ids:
            return "验收 case: 后必须跟至少一个 id（多个用逗号分隔）"
        bad = [i for i in ids if not CASE_ID_RE.match(i)]
        if bad:
            return (f"验收 case id 非法（限小写字母数字与连字符，"
                    f"多个用逗号分隔）: {bad!r}")
        return None
    if acc.startswith("manual:"):
        return None
    return ("验收必须为 case:<id>[,<id>...]（id 限小写字母数字与连字符，"
            "多个用逗号分隔）或 manual:<自由文本>")


def _emit_precheck_mark():
    """apply 角色 precheck 通过后自发 mark（A-1/B-1：脚本自发替代 AI 手打）。

    SKILL.md 旧规要求 AI 在 precheck 通过后手动 mark precheck——实测漂移
    （与 edit_item 漏打同根因），收敛到解析器自身：apply 角色 exit 0 前进
    程内直调 emit_mark。emit 角色不打点（emit 侧无活跃 apply 批）；失败
    静默不阻断校验主流程（打点诊断数据）。
    """
    try:
        import cdp_timing  # 延迟导入：cdp_timing 顶层 import cdp_parse，顶层互导成环
        cdp_timing.emit_mark("precheck")
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: cdp_parse.py --role emit|apply [--expect-base <12hex>] <批次文件>")
        print("      cdp_parse.py --gen-checksum <批次文件>"
              "（emit 产批收尾：插入/刷新 checksum 行后整批输出）")
        return 0
    # --gen-checksum：emit 侧批次生成收尾入口（角色机器化——仅 emit 设备）
    if argv[0] == "--gen-checksum":
        if len(argv) != 2:
            print("error: 用法: cdp_parse.py --gen-checksum <批次文件>")
            return EXIT_ARGS
        require_role("emit")
        try:
            with open(argv[1], encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: 批次文件不可读或非 UTF-8: {e}")
            return EXIT_ARGS
        out = with_checksum(text)
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return EXIT_OK
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
    # 角色机器化门禁：--role 即设备角色（emit 设备自检产批 / apply 设备
    # 解析执行），参数解析后、副作用发生前拦截跨设备误跑
    require_role(role)
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
    if not parse_batch(text).checksum:
        # 旧格式批次无 checksum 行：warn 兼容放行（新批次 emit 侧经
        # --gen-checksum 生成，apply 侧存在即校验）
        print("warn: 批次无 checksum 行（旧格式兼容放行；emit 产批请经 "
              "--gen-checksum 生成）")
    if role == "apply" and expect is None:
        # 方向 4：apply 角色必须显式传 --expect-base，缺失即拒批（18），
        # 不再静默跳过 base 校验（防 base 门禁被绕过后批次基座漂移）
        print("error: apply 角色必须传 --expect-base（base 拒批门禁），缺失拒绝整批")
        return EXIT_BASE_MISMATCH
    if expect is not None and not base_matches(text, expect):
        b = parse_batch(text)
        print(f"error: base 不匹配（批次 {b.base} != 本地 HEAD {expect.strip()}），整批拒绝")
        return EXIT_BASE_MISMATCH
    b = parse_batch(text)
    print(f"batch_id: {batch_id_from_text(text)}")
    print(f"mode: {b.mode} base: {b.base}")
    if role == "apply":
        _emit_precheck_mark()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())