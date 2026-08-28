"""data/verify 收据模块：写详情、读详情、趋势行、老化保留。

收据文件: data/verify/<YYYYMMDD-HHMMSS>-<batch_id>.md（markdown key-value 头 + 正文）
趋势文件: data/verify/trend.md（每批一行，保留 _TREND_KEEP 行）
注意: trend.md 不属于详情（文件名排序恒在最后，读取/老化必须显式排除）。
"""
import datetime
import re
from pathlib import Path

from cdp_paths import data_verify_dir

_DETAIL_KEEP = 50
_TREND_KEEP = 50
# 多行模式：^$ 锚定每一行（缺 MULTILINE 会导致 from_text 全默认值）
_FIELD_RE = re.compile(r"^- (\w+): (.+)$", re.MULTILINE)

_FIELDS = [
    "schema_version", "batch_id", "batch_base", "verified_commit",
    "verify_mode", "result", "build", "push_board", "acceptance",
    "elapsed_s", "summary",
]


class Receipt:
    def __init__(self, schema_version=1, batch_id="", batch_base="",
                 verified_commit="", verify_mode="board", result="fail",
                 build="skip", push_board="skip", acceptance="", elapsed_s=0,
                 summary=""):
        self.schema_version = schema_version
        self.batch_id = batch_id
        self.batch_base = batch_base
        self.verified_commit = verified_commit
        self.verify_mode = verify_mode
        self.result = result
        self.build = build
        self.push_board = push_board
        self.acceptance = acceptance
        self.elapsed_s = elapsed_s
        self.summary = summary

    @classmethod
    def from_text(cls, text):
        # 只解析 "## body" 之前的头部，防止正文中的 "- key: value" 行污染字段
        header = text.split("\n## body", 1)[0]
        r = cls()
        for m in _FIELD_RE.finditer(header):
            key, val = m.group(1), m.group(2)
            if hasattr(r, key):
                if key in ("schema_version", "elapsed_s"):
                    try:
                        setattr(r, key, int(val))
                    except ValueError:
                        pass  # 非法数值回落默认值，不崩
                else:
                    setattr(r, key, val)
        return r

    def header_lines(self):
        return "\n".join(f"- {f}: {getattr(self, f)}" for f in _FIELDS)


def _detail_files(verify_dir: Path):
    """详情文件列表（排除 trend.md），按文件名升序。"""
    return sorted(f for f in verify_dir.glob("*.md") if f.name != "trend.md")


def write_receipt(receipt, body_text):
    """写详情文件并老化，返回路径。"""
    d = data_verify_dir()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = d / f"{ts}-{receipt.batch_id}.md"
    content = receipt.header_lines() + "\n\n## body\n\n" + body_text.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    prune_details(d)
    return path


def read_receipt(path):
    return Receipt.from_text(Path(path).read_text(encoding="utf-8"))


def read_latest_receipt(verify_dir=None):
    """读最新详情（排除 trend.md）。无收据返回 None。"""
    _, r = latest_receipt_with_path(verify_dir)
    return r


def latest_receipt_with_path(verify_dir=None):
    """读最新详情（排除 trend.md），返回 (路径, Receipt)；无收据返回 (None, None)。"""
    d = verify_dir or data_verify_dir()
    files = _detail_files(d)
    if not files:
        return (None, None)
    return (files[-1], read_receipt(files[-1]))


def append_trend(timestamp, batch_id, result, stage, summary):
    d = data_verify_dir()
    trend = d / "trend.md"
    line = f"{timestamp} {batch_id} {result} {stage} {summary}\n"
    with trend.open("a", encoding="utf-8") as f:
        f.write(line)
    lines = trend.read_text(encoding="utf-8").splitlines()
    if len(lines) > _TREND_KEEP:
        trend.write_text("\n".join(lines[-_TREND_KEEP:]) + "\n", encoding="utf-8")


def read_trend_last(verify_dir=None):
    d = verify_dir or data_verify_dir()
    trend = d / "trend.md"
    if not trend.exists():
        return ""
    lines = trend.read_text(encoding="utf-8").splitlines()
    return lines[-1] if lines else ""


def prune_details(verify_dir=None):
    """详情老化保留 _DETAIL_KEEP 份（trend.md 不计入配额）。"""
    d = verify_dir or data_verify_dir()
    files = _detail_files(d)
    for old in files[: max(0, len(files) - _DETAIL_KEEP)]:
        old.unlink()