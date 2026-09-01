"""data/known-issues 已知问题登记模块：写详情、读详情、校验、索引（写时老化仅已闭环记录计配额，保留 _ISSUE_KEEP 份已闭环记录）。

问题文件: data/known-issues/<YYYYMMDD-HHMMSS>-<batch_id>-<slug>.md
（markdown key-value 头 + 正文；命名保证唯一写入，已闭环超 _ISSUE_KEEP 删最旧，
未闭环条目不计配额不删）
索引文件: data/known-issues/index.md（一行一条: issue_id origin blocking task status，
write_issue 与状态变更均重建回写；index.md 不计配额）
模板: harness/config/known-issues-template.md（头字段集必须与 _FIELDS 完全一致，
由单测强制）。
"""
import datetime
import re
from pathlib import Path

from cdp_paths import data_known_issues_dir

# 多行模式：^$ 锚定每一行（缺 MULTILINE 会导致 from_text 全默认值）
# 值用 (.*) 允许空值（如 resolved_in 未解决时留空），空值头字段也能被解析
_FIELD_RE = re.compile(r"^- (\w+): (.*)$", re.MULTILINE)
# 文件名式样: <YYYYMMDD>-<HHMMSS>-<12hex batch_id>-<slug>.md（index.md 除外）
_NAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{12}-.+\.md$")

# 问题文件保留配额（写时老化；index.md 不计配额，对照 cdp_receipt._DETAIL_KEEP）
_ISSUE_KEEP = 20

# 头字段定序：只列头字段；余项（现场/复现步骤/修法描述等自由信息）一律入正文。
_FIELDS = [
    "schema_version", "issue_id", "title", "discovered_in",
    "origin", "severity", "blocking", "blocking_reason", "status", "task",
    "resolved_in",
]

# origin / severity / status 允许取值（模板逐字段注释同源维护）
_ORIGINS = ("introduced", "pre-existing")
_ORIGIN_DEFAULT = "introduced"
_SEVERITIES = ("P0", "P1", "P2")
_SEVERITY_DEFAULT = "P2"
_STATUSES = ("open", "scheduled", "fixed", "wontfix")
_STATUS_DEFAULT = "open"

_SLUG_MAX = 40


class Issue:
    def __init__(self, schema_version=1, issue_id="", title="", discovered_in="",
                 origin=_ORIGIN_DEFAULT, severity=_SEVERITY_DEFAULT,
                 blocking=False, blocking_reason="",
                 status=_STATUS_DEFAULT, task="", resolved_in="", batch_id=""):
        self.schema_version = schema_version
        self.issue_id = issue_id
        self.title = title
        self.discovered_in = discovered_in
        self.origin = origin if origin in _ORIGINS else _ORIGIN_DEFAULT
        self.severity = severity if severity in _SEVERITIES else _SEVERITY_DEFAULT
        self.blocking = blocking
        self.blocking_reason = blocking_reason
        self.status = status if status in _STATUSES else _STATUS_DEFAULT
        self.task = task
        self.resolved_in = resolved_in
        # 命名元数据（发现批次），不属头字段，仅用于文件名 时间戳-batch_id-slug
        self.batch_id = batch_id

    @classmethod
    def from_text(cls, text):
        # 只解析 "## body" 之前的头部，防止正文中的 "- key: value" 行污染字段
        header = text.split("\n## body", 1)[0]
        r = cls()
        for m in _FIELD_RE.finditer(header):
            key, val = m.group(1), m.group(2).strip()
            if key == "blocking":
                r.blocking = val.lower() in ("true", "1", "yes")
            elif key == "schema_version":
                try:
                    r.schema_version = int(val)
                except ValueError:
                    pass  # 非法数值回落默认值，不崩
            elif key == "origin" and val not in _ORIGINS:
                continue  # 非法枚举回落默认值，不崩
            elif key == "severity" and val not in _SEVERITIES:
                continue  # 非法枚举回落默认值，不崩
            elif key == "status" and val not in _STATUSES:
                continue
            elif hasattr(r, key):
                setattr(r, key, val)
        return r

    def header_lines(self):
        return "\n".join(f"- {f}: {getattr(self, f)}" for f in _FIELDS)


def _slug_from_title(title: str) -> str:
    """由 title 派生文件名 slug：非字母数字/中文/连字符下划线一律去掉，超长截断。"""
    s = re.sub(r"\s+", "-", title).strip("-")
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]", "", s)
    return s[:_SLUG_MAX] or "issue"


def write_issue(issue, body_text, slug=""):
    """写详情文件（唯一写入：<时间戳>-<batch_id>-<slug>.md；已闭环超 _ISSUE_KEEP 删最旧）并回写 index，返回路径。

    slug 缺省由 title 派生；batch_id 来自 issue.batch_id（命名元数据，非头字段）。
    batch_id 必须为 12 位小写 hex，否则抛 ValueError——写时失败（现场可修）不留到
    promote 才暴露（畸形文件名式样会被 validate_issue 判红堵死门禁）。
    同秒同批同名冲突时追加 -1/-2 序号，绝不覆盖既有记录。
    老化与 index 重建顺序：先 prune 再 sync_index，保证 index 与文件集一致。
    """
    if not re.fullmatch(r"[0-9a-f]{12}", issue.batch_id or ""):
        raise ValueError(f"batch_id 非法: {issue.batch_id!r}（须 12 位小写 hex，"
                         f"如 d736c6283cd0；写时校验防畸形文件名堵死 promote 门禁）")
    d = data_known_issues_dir()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slug or _slug_from_title(issue.title)
    path = d / f"{ts}-{issue.batch_id}-{slug}.md"
    n = 1
    while path.exists():
        path = d / f"{ts}-{issue.batch_id}-{slug}-{n}.md"
        n += 1
    content = issue.header_lines() + "\n\n## body\n\n" + body_text.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    prune_issues(d)
    sync_index(d)
    return path


def read_issue(path):
    return Issue.from_text(Path(path).read_text(encoding="utf-8"))


def issue_files(issues_dir=None):
    """问题文件列表（显式排除 index.md，对照 cdp_receipt._detail_files 排 trend.md），升序。"""
    d = issues_dir or data_known_issues_dir()
    return sorted(p for p in d.glob("*.md") if p.name != "index.md")


def prune_issues(issues_dir=None):
    """问题文件老化：仅已闭环记录（status=fixed 且 blocking=false）计配额，
    保留 _ISSUE_KEEP 份已闭环记录删最旧；未闭环条目不计配额不删。"""
    d = issues_dir or data_known_issues_dir()
    closed = []
    for p in issue_files(d):
        i = read_issue(p)
        if i.status == "fixed" and not i.blocking:
            closed.append(p)
    for old in closed[: max(0, len(closed) - _ISSUE_KEEP)]:
        old.unlink()


def set_status(path, new_status, issues_dir=None):
    """状态变更：改文件头部 status 并重建回写 index.md。

    非法枚举抛 ValueError（变更是有意的写操作，静默回落会掩盖门禁失效）。
    """
    if new_status not in _STATUSES:
        raise ValueError(f"非法 status: {new_status!r}，允许 {_STATUSES}")
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    header, sep, body = text.partition("\n## body")
    new_header = _FIELD_RE.sub(
        lambda m: f"- status: {new_status}" if m.group(1) == "status" else m.group(0),
        header)
    p.write_text(new_header + sep + body, encoding="utf-8")
    sync_index(issues_dir)
    return p


def read_index(issues_dir=None):
    """读 index.md：一行一条 dict（issue_id origin blocking task status）。"""
    d = issues_dir or data_known_issues_dir()
    idx = d / "index.md"
    if not idx.exists():
        return []
    entries = []
    for ln in idx.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        parts = ln.split()
        if len(parts) < 5:
            continue  # 坏行跳过（validate_issue 会判红）
        entries.append({
            "issue_id": parts[0], "origin": parts[1],
            "blocking": parts[2].lower() in ("true", "1", "yes"),
            "task": parts[3], "status": parts[4],
        })
    return entries


def sync_index(issues_dir=None):
    """按文件集重建 index.md（一行一条: issue_id origin blocking task status）。"""
    d = issues_dir or data_known_issues_dir()
    lines = []
    for p in issue_files(d):
        issue = read_issue(p)
        lines.append(f"{issue.issue_id} {issue.origin} "
                     f"{str(issue.blocking).lower()} {issue.task} {issue.status}")
    (d / "index.md").write_text("\n".join(lines) + ("\n" if lines else ""),
                                encoding="utf-8")


def validate_issue(path, issues_dir=None):
    """判红已知问题文件，返回错误列表（空即通过）。

    检查: 头字段齐备、枚举合法、blocking=true 时 blocking_reason 非空、
    文件名式样、index 与文件集一致（双向：index 覆盖全部文件且字段匹配）。
    """
    d = Path(issues_dir) if issues_dir else data_known_issues_dir()
    p = Path(path)
    errs = []
    if not _NAME_RE.match(p.name):
        errs.append(f"文件名式样非法: {p.name}（须 <YYYYMMDD>-<HHMMSS>-<12hex>-<slug>.md）")
    text = None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        errs.append(f"文件不可读: {e}")
    if text is not None:
        header, sep, body = text.partition("\n## body")
        fields = {m.group(1): m.group(2).strip() for m in _FIELD_RE.finditer(header)}

        for f in _FIELDS:
            if f not in fields:
                errs.append(f"头字段缺失: {f}")

        origin = fields.get("origin", _ORIGIN_DEFAULT)
        if origin not in _ORIGINS:
            errs.append(f"origin 非法: {origin!r}，允许 {_ORIGINS}")
        severity = fields.get("severity", _SEVERITY_DEFAULT)
        if severity not in _SEVERITIES:
            errs.append(f"severity 非法: {severity!r}，允许 {_SEVERITIES}")
        status = fields.get("status", _STATUS_DEFAULT)
        if status not in _STATUSES:
            errs.append(f"status 非法: {status!r}，允许 {_STATUSES}")
        blocking = fields.get("blocking", "false").lower() in ("true", "1", "yes")
        if blocking and not fields.get("blocking_reason", ""):
            errs.append("blocking=true 但 blocking_reason 为空")
        # task 含空白判红：read_index 按空格切分 index 行，task 内空白会列错位
        if re.search(r"\s", fields.get("task", "")):
            errs.append(f"task 含空白: {fields['task']!r}（index 按空格切分会错位）")

    # index 与文件集一致（文件缺失/多余均检出；目标文件字段须与 index 匹配）
    entries = read_index(d)
    idx_by_id = {e["issue_id"]: e for e in entries}
    file_ids = set()
    for f in issue_files(d):
        i = read_issue(f)
        file_ids.add(i.issue_id)
        if text is not None and p.resolve() == f.resolve():
            entry = idx_by_id.get(i.issue_id)
            if entry is None:
                errs.append(f"index 缺该问题条目: {i.issue_id}")
            else:
                for key, cur in (("origin", i.origin), ("task", i.task),
                                 ("status", i.status)):
                    if entry[key] != cur:
                        errs.append(f"index[{key}]={entry[key]} != 文件头 {cur}")
                if entry["blocking"] != i.blocking:
                    errs.append(f"index[blocking]={entry['blocking']} != 文件头 {i.blocking}")
    index_ids = set(idx_by_id)
    if index_ids != file_ids:
        only_idx = index_ids - file_ids
        only_file = file_ids - index_ids
        if only_idx:
            errs.append(f"index 有多余条目(无对应文件): {sorted(only_idx)}")
        if only_file:
            errs.append(f"index 缺文件条目: {sorted(only_file)}")
    return errs


def template_path() -> Path:
    """模板文件路径（静态资产，按模块相对位置解析，不受 CDP_PROJECT_ROOT 影响）。"""
    # 本文件位于 harness/skills/cross-device/lib/python/，parents[4] 即 harness/
    return Path(__file__).resolve().parents[4] / "config" / "known-issues-template.md"


def template_header_fields(template=None) -> list:
    """从模板提取头字段名（与 _FIELDS 同源校验，单测强制一致）。"""
    tmpl = Path(template) if template else template_path()
    header = tmpl.read_text(encoding="utf-8").split("\n## body", 1)[0]
    return [m.group(1) for m in _FIELD_RE.finditer(header)]
