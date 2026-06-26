#!/bin/bash
set -uo pipefail

# ============================================================================
# detect_test_env.sh — 探测测试命令(TEST_CMD)和 PYTHONPATH
# 规则详见: engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
# 输出:    STDOUT 两行
#            TEST_CMD=<命令>
#            PYTHONPATH=<冒号分隔路径>
# 退出码:  0=探测成功; 3=参数/环境错误（未找到测试目录或 PYTHONPATH 为空）
# ============================================================================

# --- 锚点 + 公共库（bootstrap 统一入口）-------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "detect_test_env"

# ============================================================================
# 参数解析
# ============================================================================
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            echo "Usage: bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh"
            echo "  无参数。输出 TEST_CMD 和 PYTHONPATH 两行到 STDOUT。"
            harness_exit 0 ;;
        *) log_error "未知参数: $arg"; harness_exit 3 ;;
    esac
done

cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; harness_exit 3; }

# ============================================================================
# Step 1: 获取 PYTHONPATH（复用 harness_pythonpath，符合 PATH-001 DRY）
# ============================================================================
step_begin "获取 PYTHONPATH"

PYTHONPATH_OUT=$(harness_pythonpath)

if [ -z "$PYTHONPATH_OUT" ]; then
    log_error "harness_pythonpath 返回空（检查 harness-paths.conf 中 PYTHON_PATH_ROOTS）"
    harness_exit 3
fi

log_info "PYTHONPATH=$PYTHONPATH_OUT"
step_end 0

# ============================================================================
# Step 2: 探测 pytest 配置，构造 TEST_CMD
# ============================================================================
step_begin "探测测试配置"

TEST_CMD=""

# 优先级 1: pytest.ini
if [ -f "$REPO_ROOT/pytest.ini" ]; then
    # 提取 [pytest] 段的 testpaths（如果有）
    PYTEST_TESTPATHS=$(sed -n '/^\[pytest\]/,/^\[/p' "$REPO_ROOT/pytest.ini" \
        | grep -iE '^testpaths' | head -1 | cut -d= -f2 | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest -v"
    fi
    log_info "发现 pytest.ini，TEST_CMD=$TEST_CMD"

# 优先级 2: pyproject.toml [tool.pytest.ini_options]
elif [ -f "$REPO_ROOT/pyproject.toml" ]; then
    PYTEST_TESTPATHS=$(sed -n '/\[tool.pytest.ini_options\]/,/^\[/p' "$REPO_ROOT/pyproject.toml" \
        | grep -iE 'testpaths' | head -1 | sed 's/.*=.*\[\(.*\)\]/\1/' | tr -d '"' | tr ',' ' ' | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
    fi
    log_info "发现 pyproject.toml，TEST_CMD=$TEST_CMD"

# 优先级 3: setup.cfg [tool:pytest]
elif [ -f "$REPO_ROOT/setup.cfg" ]; then
    PYTEST_TESTPATHS=$(sed -n '/\[tool:pytest\]/,/^\[/p' "$REPO_ROOT/setup.cfg" \
        | grep -iE '^testpaths' | head -1 | cut -d= -f2 | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
    fi
    log_info "发现 setup.cfg，TEST_CMD=$TEST_CMD"

# 优先级 4: 无配置，检查是否有测试目录
else
    # 扫描 engineering/ 下是否有 tests/ 目录
    TEST_DIRS=$(find engineering/ -type d -name "tests" 2>/dev/null | head -5 || true)
    if [ -n "$TEST_DIRS" ]; then
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
        log_info "无 pytest 配置，发现测试目录，TEST_CMD=$TEST_CMD"
    else
        log_error "未找到 pytest 配置，也未找到 engineering/*/tests/ 目录"
        harness_exit 3
    fi
fi

step_end 0

# ============================================================================
# 输出结果（两行，供 AI 解析）
# ============================================================================
echo "TEST_CMD=$TEST_CMD"
echo "PYTHONPATH=$PYTHONPATH_OUT"

log_result "探测完成" "TEST_CMD=$TEST_CMD" "PYTHONPATH=$PYTHONPATH_OUT"

harness_exit 0
