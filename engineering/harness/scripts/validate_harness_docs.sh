#!/bin/bash
set -uo pipefail

# ============================================================================
# validate_harness_docs.sh — harness 文档/契约层静态一致性校验
# 职责边界：
#   - README 导航链接存在性（引用的文件路径是否存在）
#   - 各子目录 README 文件清单与实际目录文件是否一致（漏登记检测）
#   - template 中 PlantUML @startuml/@enduml 配对闭合
#   - template UML 块内是否含花括号占位符 {{ 或 {模块
#   - workflow contract 头部完整性（YAML front matter 含 name/description）
# 不负责：
#   - bash 代码语义 / YAML 解析 / 脚本运行时行为
# 详见: docs/plans/2026-06-20-harness-engineering-midterm-governance-and-script-reliability-plan.md Task 8
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_harness_docs"

HARNESS_DIR="$(harness_path HARNESS_DIR)"

# 累计告警计数
WARN_COUNT=0
SCAN_COUNT=0

# --- 辅助：记录一条告警 ------------------------------------------------------
_report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# ============================================================================
# Step 1: README 导航链接存在性
# ============================================================================
step_begin "README 导航链接存在性"

# 扫描 harness 下所有 README.md，提取 Markdown 链接中的相对路径并校验存在性
# 链接形如 [text](./path) 或 [text](./path.md) 或 [text](path/)
scan_readme_links() {
    local readme="$1"
    local dir
    dir="$(dirname "$readme")"
    local lineno=0
    local line
    while IFS= read -r line; do
        lineno=$((lineno + 1))
        # 提取所有 (相对路径) 形式的链接目标；忽略 http(s) / 锚点
        # 形如 ](./xxx) 或 ](xxx) 或 ](./xxx#anchor)
        local targets
        targets=$(printf '%s' "$line" \
            | grep -oE '\]\([^)]+\)' \
            | sed -E 's/^\]\(//; s/\)$//')
        [ -z "$targets" ] && continue
        local t
        while IFS= read -r t; do
            [ -z "$t" ] && continue
            # 跳过 http/https/mailto
            case "$t" in
                http://*|https://*|mailto:*) continue ;;
            esac
            # 去掉锚点
            local path="${t%%#*}"
            [ -z "$path" ] && continue
            # 解析相对路径
            local full="$dir/$path"
            # 规范化（去除 ./ 和多余分隔符）
            local norm
            norm=$(cd "$dir" 2>/dev/null && { [ -e "$path" ] && realpath "$path" 2>/dev/null || echo "MISS"; } || echo "MISS")
            if [ "$norm" = "MISS" ]; then
                _report_warn "$readme:$lineno" "链接目标不存在: $t"
            else
                SCAN_COUNT=$((SCAN_COUNT + 1))
            fi
        done <<< "$targets"
    done < "$readme"
}

# 收集所有 README.md
README_FILES=()
while IFS= read -r f; do
    README_FILES+=("$f")
done < <(find "$HARNESS_DIR" -name 'README.md' -type f \
    -not -path '*/__pycache__/*' \
    -not -path '*/.pytest_cache/*' \
    2>/dev/null)

if [ "${#README_FILES[@]}" -eq 0 ]; then
    log_warn "未发现任何 README.md，跳过链接扫描"
else
    for r in "${README_FILES[@]}"; do
        log_info "扫描 README: ${r#$HARNESS_DIR/}"
        scan_readme_links "$r"
    done
fi

log_info "README 链接校验完成：通过 $SCAN_COUNT 条，告警 $WARN_COUNT 条"
step_end 0

# ============================================================================
# Step 2: 子目录 README 文件清单与实际目录一致性
# ============================================================================
step_begin "README 文件清单与实际目录一致性"

# 对每个含 README.md 的目录，比对 README 中引用的同目录文件 与 目录顶层实际文件。
# 报告：实际存在但 README 未登记的普通文件（漏登记）。
# 不报告：子目录、README.md 自身、.gitkeep（链接目标缺失由 Step 1 覆盖）。
scan_readme_inventory() {
    local readme="$1"
    local dir
    dir="$(dirname "$readme")"

    # 跳过 workflow 叶子目录：其 README 按约定仅作极简入口（指向 WORKFLOW.md），
    # 不登记 *.sh 文件清单（脚本登记在 WORKFLOW.md），故不纳入文件清单比对。
    if [ -f "$dir/WORKFLOW.md" ]; then
        log_info "  跳过 workflow 叶子目录（README 为极简入口，不登记文件清单）"
        return 0
    fi

    # 收集 README 中引用的"同目录普通文件" basename（含 / 的视为子目录文件，跳过）
    local referenced
    referenced=$(grep -oE '\]\(\.?/?[^):#]+\)' "$readme" 2>/dev/null \
        | sed -E 's/^\]\(//; s/\)$//; s#^\./##' \
        | grep -vE '/' \
        | sort -u)

    # 实际目录顶层普通文件
    local actual_file bn
    while IFS= read -r actual_file; do
        [ -z "$actual_file" ] && continue
        bn="$(basename "$actual_file")"
        case "$bn" in
            README.md|.gitkeep) continue ;;
        esac
        if printf '%s\n' "$referenced" | grep -qxF "$bn"; then
            SCAN_COUNT=$((SCAN_COUNT + 1))
        else
            _report_warn "$readme" "目录文件未在 README 文件清单中登记: $bn"
        fi
    done < <(find "$dir" -maxdepth 1 -type f 2>/dev/null)
}

if [ "${#README_FILES[@]}" -eq 0 ]; then
    log_warn "未发现任何 README.md，跳过文件清单一致性检查"
else
    for r in "${README_FILES[@]}"; do
        log_info "比对目录文件清单: ${r#$HARNESS_DIR/}"
        scan_readme_inventory "$r"
    done
fi

step_end 0

# ============================================================================
# Step 3: template PlantUML 合法性
# ============================================================================
step_begin "template PlantUML 合法性"

# 扫描 templates/ 与 docs/ 下所有 .md 文件中的 plantuml fenced code block
# 检查项 A：每个 @startuml 必须在同一 fenced block 内有对应的 @enduml
# 检查项 B：UML 块内禁止花括号占位符 {{ 或 {模块 形式
scan_plantuml_in_file() {
    local file="$1"
    local lineno=0 in_puml=0 start_line=0 has_start=0 has_end=0
    local line
    while IFS= read -r line; do
        lineno=$((lineno + 1))
        # 检测 fenced code block 开 fence (三个反引号 + plantuml)
        if [ "$in_puml" -eq 0 ]; then
            if printf '%s' "$line" | grep -qE '^\s*```plantuml\s*$'; then
                in_puml=1
                start_line=$lineno
                has_start=0
                has_end=0
            fi
            continue
        fi
        # in_puml=1
        # close fence
        if printf '%s' "$line" | grep -qE '^\s*```\s*$'; then
            # 块结束，校验闭合
            if [ "$has_start" -eq 1 ] && [ "$has_end" -eq 0 ]; then
                _report_warn "$file:$start_line" "PlantUML 块 @startuml 未配对 @enduml"
            fi
            if [ "$has_start" -eq 0 ] && [ "$has_end" -eq 1 ]; then
                _report_warn "$file:$start_line" "PlantUML 块出现 @enduml 但缺少 @startuml"
            fi
            in_puml=0
            continue
        fi
        # 块内：检测 startuml/enduml
        if printf '%s' "$line" | grep -qE '@startuml'; then
            has_start=1
        fi
        if printf '%s' "$line" | grep -qE '@enduml'; then
            has_end=1
        fi
        # 块内：检测花括号占位符 {{ 或 {模块 / {中文...}
        # 双花括号 {{ 直接命中
        if printf '%s' "$line" | grep -qE '\{\{'; then
            _report_warn "$file:$lineno" "PlantUML 块内出现双花括号占位符 {{...}}（违反 RID-PLANTUML-001 规则2）"
        fi
        # 单花括号占位符 {非空文字} 紧凑形式（占位符）；
        # 注意：PlantUML 自身合法的 package { / object { 块定义的 { 一般出现在行尾或独立行，
        # 而占位符是 {文字} 紧凑形式（中间有 1-30 个非 } 字符且不以 { 或 } 结尾）。
        # 使用 LC_ALL=C 避免 UTF-8 collation 报错；模式仅匹配 ASCII 字母/中文/下划线开头的紧凑占位符。
        if printf '%s' "$line" | LC_ALL=C grep -qE '\{[A-Za-z_][^{}]{0,30}\}'; then
            _report_warn "$file:$lineno" "PlantUML 块内出现花括号占位符 {xxx}（违反 RID-PLANTUML-001 规则2）"
        fi
    done < "$file"
    # 文件结束时若仍在 puml 块内
    if [ "$in_puml" -eq 1 ]; then
            _report_warn "$file:$start_line" "PlantUML fenced code block 未闭合（缺少结束 fence）"
    fi
}

# 扫描 templates/*.md 与 harness 下所有 README.md、CONTROL-CHARTER.md
# 排除：rules/plantuml.md（规则定义本身含反例，不参与扫描）
PUML_TARGETS=()
while IFS= read -r f; do
    case "$f" in
        */rules/plantuml.md) continue ;;
    esac
    PUML_TARGETS+=("$f")
done < <(find "$HARNESS_DIR/templates" "$HARNESS_DIR" -name '*.md' -type f 2>/dev/null | sort -u)

if [ "${#PUML_TARGETS[@]}" -eq 0 ]; then
    log_warn "未发现任何 .md 文件，跳过 PlantUML 扫描"
else
    for f in "${PUML_TARGETS[@]}"; do
        log_info "扫描 PlantUML: ${f#$HARNESS_DIR/}"
        scan_plantuml_in_file "$f"
    done
fi

step_end 0

# ============================================================================
# Step 4: workflow contract 头部完整性
# ============================================================================
step_begin "workflow contract 头部完整性"

# 每个 workflows/*/WORKFLOW.md 必须有 YAML front matter（--- 起止），且含 name: 与 description:
WORKFLOW_FILES=()
while IFS= read -r f; do
    WORKFLOW_FILES+=("$f")
done < <(find "$HARNESS_DIR/workflows" -name 'WORKFLOW.md' -type f 2>/dev/null)

if [ "${#WORKFLOW_FILES[@]}" -eq 0 ]; then
    log_warn "未发现任何 WORKFLOW.md，跳过 contract 校验"
else
    for wf in "${WORKFLOW_FILES[@]}"; do
        local_name="${wf#$HARNESS_DIR/}"
        log_info "扫描 contract: $local_name"
        # 第一行必须是 ---
        first_line=$(head -n 1 "$wf")
        if [ "$first_line" != "---" ]; then
            _report_warn "$wf:1" "缺少 YAML front matter 起始 ---"
            continue
        fi
        # 找到第二个 ---（结束）
        end_line=$(grep -nE '^---\s*$' "$wf" | sed -n '2p' | cut -d: -f1)
        if [ -z "$end_line" ]; then
            _report_warn "$wf:1" "YAML front matter 未闭合（缺少第二个 ---）"
            continue
        fi
        # 在 front matter 内检查 name / description
        fm_content=$(head -n "$end_line" "$wf")
        if ! printf '%s' "$fm_content" | grep -qE '^name:'; then
            _report_warn "$wf:1" "YAML front matter 缺少 name 字段"
        fi
        if ! printf '%s' "$fm_content" | grep -qE '^description:'; then
            _report_warn "$wf:1" "YAML front matter 缺少 description 字段"
        fi
        SCAN_COUNT=$((SCAN_COUNT + 1))
    done
fi

step_end 0

# ============================================================================
# 汇总
# ============================================================================
if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_harness_docs 结果" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_harness_docs 结果" "warns=0" "verdict=PASS"
harness_exit 0
