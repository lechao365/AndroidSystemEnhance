#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-repo-registry.sh — LcHarness Repo Registry 读写工具
#
# 职责:
#   管理 ~/.local/share/lcharness/registry.yaml，提供 CRUD 子命令供
#   control-plane 组件操作 repo 注册表。
#
# 用法:
#   lc-repo-registry.sh add <path> --profile <name>
#   lc-repo-registry.sh remove <id>
#   lc-repo-registry.sh list
#   lc-repo-registry.sh get <id>
#   lc-repo-registry.sh update <id> <field> <value>
#   lc-repo-registry.sh exists <id>
#
# 退出码:
#   0 成功
#   1 参数错误 / 数据错误
#   3 环境错误（无 python3 等）
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-repo-registry"

# ============================================================================
# 全局常量
# ============================================================================
REGISTRY_DIR="${HOME}/.local/share/lcharness"
REGISTRY_FILE="${REGISTRY_DIR}/registry.yaml"
LOCK_FILE="${REGISTRY_DIR}/registry.yaml.lock"

VALID_STATES=("attached" "injected" "healthy" "stale" "broken")

# ============================================================================
# 工具函数
# ============================================================================

# 检查 python3 是否可用
check_python3() {
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "未找到 python3，无法操作 registry"
        harness_exit 3
    fi
}

# 获取当前 ISO 时间戳
now_iso() {
    date '+%Y-%m-%dT%H:%M:%S%z'
}

# 计算 path 的短 MD5(id=md5sum(path)[:12])
compute_id() {
    local path="$1"
    printf '%s' "$path" | python3 -c "
import hashlib, sys
path = sys.stdin.read()
print(hashlib.md5(path.encode('utf-8')).hexdigest()[:12])
"
}

# 确保 registry 所在目录存在
ensure_registry_dir() {
    mkdir -p "$REGISTRY_DIR"
}

# ============================================================================
# 文件锁
# ============================================================================

acquire_lock() {
    local lock_file="$LOCK_FILE"
    local timeout=5

    # 确保 lock 文件所在目录存在
    ensure_registry_dir

    if command -v flock >/dev/null 2>&1; then
        exec 200>"$lock_file"
        flock -w "$timeout" 200 || {
            log_error "无法获取文件锁 (flock 超时 ${timeout}s): $lock_file"
            return 1
        }
    else
        local ts=$SECONDS
        while ! mkdir "$lock_file.lockdir" 2>/dev/null; do
            if (( SECONDS - ts > timeout )); then
                log_error "无法获取文件锁 (mkdir 超时 ${timeout}s): $lock_file.lockdir"
                return 1
            fi
            sleep 0.1
        done
    fi
    return 0
}

release_lock() {
    if command -v flock >/dev/null 2>&1; then
        flock -u 200 2>/dev/null
    else
        rm -rf "$LOCK_FILE.lockdir" 2>/dev/null
    fi
}

# ============================================================================
# Registry 文件读写（python3 嵌入）
# ============================================================================

# 读取 registry 全部内容，输出 JSON 到 stdout
# 如果文件不存在，输出空的 registry 结构
read_registry_json() {
    check_python3
    if [ ! -f "$REGISTRY_FILE" ]; then
        echo '{"version": 1, "repos": []}'
        return 0
    fi
    python3 - "$REGISTRY_FILE" <<'PY'
import json
import sys
import yaml

registry_path = sys.argv[1]
try:
    with open(registry_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # 确保基本结构
    if "version" not in data:
        data["version"] = 1
    if "repos" not in data or not isinstance(data["repos"], list):
        data["repos"] = []
    print(json.dumps(data))
except Exception as e:
    print(json.dumps({"version": 1, "repos": [], "_error": str(e)}))
PY
}

# 将 JSON 数据写回 registry.yaml（写入前备份，写后校验）
write_registry_json() {
    local json_data="$1"
    check_python3

    ensure_registry_dir

    # 写入前备份
    if [ -f "$REGISTRY_FILE" ]; then
        cp "$REGISTRY_FILE" "${REGISTRY_FILE}.bak"
    fi

    # 通过 python3 写入 YAML
    python3 - "$REGISTRY_FILE" "$json_data" <<'PY'
import json
import sys
import yaml

registry_path = sys.argv[1]
json_data = sys.argv[2]

try:
    data = json.loads(json_data)

    # 写入前校验
    errors = []

    # version 为正整数
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append(f"version 非法: {version!r}")

    # repos 为列表（允许空）
    repos = data.get("repos")
    if not isinstance(repos, list):
        errors.append("repos 必须是列表")

    # 每个 entry 校验
    valid_states = {"attached", "injected", "healthy", "stale", "broken"}
    if isinstance(repos, list):
        for idx, entry in enumerate(repos):
            if not isinstance(entry, dict):
                errors.append(f"repos[{idx}] 非对象")
                continue
            for field in ("id", "path", "profile", "overlay_root", "state"):
                if field not in entry or not isinstance(entry.get(field), str) or not entry[field].strip():
                    errors.append(f"repos[{idx}].{field} 不能为空")
            state = entry.get("state", "")
            if state not in valid_states:
                errors.append(f"repos[{idx}].state 非法: {state!r}，允许值: {sorted(valid_states)}")

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)

    with open(registry_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print("OK", end="")
except json.JSONDecodeError as e:
    print(f"JSON 解析失败: {e}", file=sys.stderr)
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"YAML 写入失败: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"写入 registry 失败: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

# ============================================================================
# 子命令实现
# ============================================================================

# add <path> --profile <name>
cmd_add() {
    local repo_path="" profile_name=""

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --profile)
                shift
                profile_name="$1"
                ;;
            --*)
                log_error "未知选项: $1"
                echo "用法: lc-repo-registry.sh add <path> --profile <name>"
                harness_exit 1
                ;;
            *)
                if [ -z "$repo_path" ]; then
                    repo_path="$1"
                else
                    log_error "多余的参数: $1"
                    harness_exit 1
                fi
                ;;
        esac
        shift
    done

    # 参数校验
    if [ -z "$repo_path" ]; then
        log_error "缺少 <path> 参数"
        echo "用法: lc-repo-registry.sh add <path> --profile <name>"
        harness_exit 1
    fi
    if [ -z "$profile_name" ]; then
        log_error "缺少 --profile <name> 参数"
        echo "用法: lc-repo-registry.sh add <path> --profile <name>"
        harness_exit 1
    fi

    # 解析绝对路径
    repo_path="$(realpath -m "$repo_path" 2>/dev/null || realpath "$repo_path" 2>/dev/null || echo "$repo_path")"

    # 验证路径可读
    if [ ! -d "$repo_path" ]; then
        log_error "路径不是目录或不存在: $repo_path"
        harness_exit 1
    fi
    if [ ! -r "$repo_path" ]; then
        log_error "路径不可读: $repo_path"
        harness_exit 1
    fi

    # 获取锁
    acquire_lock || harness_exit 1

    # 读取现有 registry
    local registry_json
    registry_json=$(read_registry_json)
    local read_rc=$?
    if [ "$read_rc" -ne 0 ]; then
        release_lock
        harness_exit 1
    fi

    # 检查重复 path
    local existing
    existing=$(echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data.get('repos', []):
    if r.get('path') == '${repo_path}':
        print(r['id'])
        break
" 2>/dev/null)
    if [ -n "$existing" ]; then
        log_error "仓库路径已注册: $repo_path (id=$existing)"
        release_lock
        harness_exit 1
    fi

    # 计算 id
    local repo_id
    repo_id=$(compute_id "$repo_path")

    # 构造 overlay_root
    local overlay_root="${REGISTRY_DIR}/overlays/${repo_id}"

    # 添加条目
    local now
    now=$(now_iso)

    local new_json
    new_json=$(echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
entry = {
    'id': '${repo_id}',
    'path': '${repo_path}',
    'profile': '${profile_name}',
    'overlay_root': '${overlay_root}',
    'state': 'attached',
    'attached_at': '${now}',
    'last_reconcile': '',
    'health': {
        'last_check': '',
        'result': ''
    }
}
data.setdefault('repos', []).append(entry)
print(json.dumps(data))
" 2>/dev/null)
    local py_rc=$?
    if [ "$py_rc" -ne 0 ] || [ -z "$new_json" ]; then
        log_error "添加条目时 python3 处理失败"
        release_lock
        harness_exit 1
    fi

    # 写入
    local write_result
    write_result=$(write_registry_json "$new_json" 2>&1)
    local write_rc=$?
    if [ "$write_rc" -ne 0 ]; then
        log_error "写入 registry 失败: $write_result"
        # 恢复备份
        if [ -f "${REGISTRY_FILE}.bak" ]; then
            cp "${REGISTRY_FILE}.bak" "$REGISTRY_FILE"
            log_info "已从备份恢复: ${REGISTRY_FILE}.bak"
        fi
        release_lock
        harness_exit 1
    fi

    release_lock
    echo "$repo_id"
    log_info "已注册仓库: $repo_path (id=$repo_id, profile=$profile_name)"
    harness_exit 0
}

# remove <id>
cmd_remove() {
    local target_id="$1"
    if [ -z "$target_id" ]; then
        log_error "缺少 <id> 参数"
        echo "用法: lc-repo-registry.sh remove <id>"
        harness_exit 1
    fi

    acquire_lock || harness_exit 1

    local registry_json
    registry_json=$(read_registry_json)
    local read_rc=$?
    if [ "$read_rc" -ne 0 ]; then
        release_lock
        harness_exit 1
    fi

    # 查找并删除
    local new_json
    new_json=$(echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
orig_len = len(data.get('repos', []))
data['repos'] = [r for r in data.get('repos', []) if r.get('id') != '${target_id}']
new_len = len(data['repos'])
if new_len == orig_len:
    print('NOT_FOUND', end='')
else:
    print(json.dumps(data), end='')
" 2>/dev/null)
    local py_rc=$?

    if [ "$py_rc" -ne 0 ] || [ -z "$new_json" ]; then
        log_error "删除条目时 python3 处理失败"
        release_lock
        harness_exit 1
    fi

    if [ "$new_json" = "NOT_FOUND" ]; then
        log_error "未找到 id: $target_id"
        release_lock
        harness_exit 1
    fi

    local write_result
    write_result=$(write_registry_json "$new_json" 2>&1)
    local write_rc=$?
    if [ "$write_rc" -ne 0 ]; then
        log_error "写入 registry 失败: $write_result"
        if [ -f "${REGISTRY_FILE}.bak" ]; then
            cp "${REGISTRY_FILE}.bak" "$REGISTRY_FILE"
            log_info "已从备份恢复: ${REGISTRY_FILE}.bak"
        fi
        release_lock
        harness_exit 1
    fi

    release_lock
    log_info "已移除仓库: $target_id"
    harness_exit 0
}

# list
cmd_list() {
    local registry_json
    registry_json=$(read_registry_json)

    # 输出表格: id \tab path \tab profile \tab state
    echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
repos = data.get('repos', [])
if not repos:
    # 空表
    sys.exit(0)
for r in repos:
    line = '{}\t{}\t{}\t{}'.format(
        r.get('id', ''),
        r.get('path', ''),
        r.get('profile', ''),
        r.get('state', '')
    )
    print(line)
"
    harness_exit 0
}

# get <id>
cmd_get() {
    local target_id="$1"
    if [ -z "$target_id" ]; then
        log_error "缺少 <id> 参数"
        echo "用法: lc-repo-registry.sh get <id>"
        harness_exit 1
    fi

    local registry_json
    registry_json=$(read_registry_json)

    local found
    found=$(echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data.get('repos', []):
    if r.get('id') == '${target_id}':
        import sys
        for k, v in r.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    print(f'{k}.{sk}={sv}')
            else:
                print(f'{k}={v}')
        sys.exit(0)
sys.exit(1)
")
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "未找到 id: $target_id"
        harness_exit 1
    fi
    echo "$found"
    harness_exit 0
}

# update <id> <field> <value>
cmd_update() {
    local target_id="$1" field="$2" value="$3"

    if [ -z "$target_id" ] || [ -z "$field" ] || [ -z "$value" ]; then
        log_error "缺少参数: update <id> <field> <value>"
        echo "用法: lc-repo-registry.sh update <id> <field> <value>"
        harness_exit 1
    fi

    # 只允许更新特定字段
    local allowed_fields=("state" "last_reconcile" "health.result" "health.last_check")
    local field_valid=false
    for af in "${allowed_fields[@]}"; do
        if [ "$af" = "$field" ]; then
            field_valid=true
            break
        fi
    done
    if [ "$field_valid" = false ]; then
        log_error "不允许更新字段: $field"
        log_error "允许的字段: ${allowed_fields[*]}"
        harness_exit 1
    fi

    # state 必须是合法枚举值
    if [ "$field" = "state" ]; then
        local state_valid=false
        for vs in "${VALID_STATES[@]}"; do
            if [ "$vs" = "$value" ]; then
                state_valid=true
                break
            fi
        done
        if [ "$state_valid" = false ]; then
            log_error "state 非法: $value，允许值: ${VALID_STATES[*]}"
            harness_exit 1
        fi
    fi

    acquire_lock || harness_exit 1

    local registry_json
    registry_json=$(read_registry_json)

    # 在 python3 中执行更新
    local new_json
    new_json=$(echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
target_id = '${target_id}'
field = '${field}'
value = '${value}'

found = False
for r in data.get('repos', []):
    if r.get('id') == target_id:
        found = True
        if '.' in field:
            # 嵌套字段如 health.result
            parts = field.split('.')
            parent = r
            for p in parts[:-1]:
                if p not in parent or not isinstance(parent[p], dict):
                    parent[p] = {}
                parent = parent[p]
            parent[parts[-1]] = value
        else:
            r[field] = value
        break

if not found:
    print('NOT_FOUND', end='')
    sys.exit(0)
print(json.dumps(data), end='')
" 2>/dev/null)
    local py_rc=$?

    if [ "$py_rc" -ne 0 ] || [ -z "$new_json" ]; then
        log_error "更新条目时 python3 处理失败"
        release_lock
        harness_exit 1
    fi

    if [ "$new_json" = "NOT_FOUND" ]; then
        log_error "未找到 id: $target_id"
        release_lock
        harness_exit 1
    fi

    local write_result
    write_result=$(write_registry_json "$new_json" 2>&1)
    local write_rc=$?
    if [ "$write_rc" -ne 0 ]; then
        log_error "写入 registry 失败: $write_result"
        if [ -f "${REGISTRY_FILE}.bak" ]; then
            cp "${REGISTRY_FILE}.bak" "$REGISTRY_FILE"
            log_info "已从备份恢复: ${REGISTRY_FILE}.bak"
        fi
        release_lock
        harness_exit 1
    fi

    release_lock
    log_info "已更新仓库 $target_id: $field=$value"
    harness_exit 0
}

# exists <id>
cmd_exists() {
    local target_id="$1"
    if [ -z "$target_id" ]; then
        log_error "缺少 <id> 参数"
        echo "用法: lc-repo-registry.sh exists <id>"
        harness_exit 1
    fi

    local registry_json
    registry_json=$(read_registry_json)

    echo "$registry_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data.get('repos', []):
    if r.get('id') == '${target_id}':
        sys.exit(0)
sys.exit(1)
"
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        harness_exit 0
    else
        harness_exit 1
    fi
}

# ============================================================================
# 用法帮助
# ============================================================================

print_usage() {
    cat <<EOF
用法: $(basename "$0") <subcommand> [args]

子命令:
  add <path> --profile <name>   注册仓库，生成 id，输出 id
  remove <id>                   从 registry 删除条目
  list                          表格输出: id  path  profile  state
  get <id>                      key=value 格式输出条目
  update <id> <field> <value>   更新指定字段
  exists <id>                   检查 id 是否存在

退出码:
  0  成功
  1  参数错误 / 数据错误
  3  环境错误（无 python3）
EOF
}

# ============================================================================
# 主入口
# ============================================================================

main() {
    if [ $# -lt 1 ]; then
        print_usage
        harness_exit 1
    fi

    local subcommand="$1"
    shift

    case "$subcommand" in
        add)
            cmd_add "$@"
            ;;
        remove)
            cmd_remove "$@"
            ;;
        list)
            cmd_list
            ;;
        get)
            cmd_get "$@"
            ;;
        update)
            cmd_update "$@"
            ;;
        exists)
            cmd_exists "$@"
            ;;
        --help|-h)
            print_usage
            harness_exit 0
            ;;
        *)
            log_error "未知子命令: $subcommand"
            print_usage
            harness_exit 1
            ;;
    esac
}

main "$@"