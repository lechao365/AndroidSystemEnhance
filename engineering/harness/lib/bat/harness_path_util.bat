@echo off
REM ============================================================================
REM harness_path_util.bat -- Windows 批处理统一路径工具
REM 规则详见: engineering/harness/rules/path-management.md (PATH-001)
REM
REM 设计说明:
REM   bat 无函数返回值/跨脚本函数复用，采用「加载即设置变量」模式。
REM   call 本文件后，所有 HARNESS_PATH_<KEY> 变量在调用者作用域可用。
REM
REM 用法（被其他 bat 脚本 call）:
REM   set "SCRIPT_DIR=%~dp0"
REM   set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
REM   call "%SCRIPT_DIR%\..\..\lib\bat\harness_path_util.bat"
REM   echo %REPO_ROOT%
REM   echo %HARNESS_PATH_HOST_LOG_DIR%
REM   echo %HARNESS_PATH_PYTHONPATH%
REM
REM 变量约定:
REM   REPO_ROOT                 -- 仓库根绝对路径
REM   HARNESS_PATH_<KEY>        -- paths.conf 中 KEY 对应的绝对路径（反斜杠）
REM   HARNESS_PATH_PYTHONPATH   -- 拼好的 PYTHONPATH（分号分隔，Windows 风格）
REM ============================================================================

setlocal enabledelayedexpansion

REM --- 定位 REPO_ROOT（从本文件位置向上找 AGENTS.md）---
set "_H_PATH_DIR=%~dp0"
set "_H_PATH_DIR=!_H_PATH_DIR:~0,-1!"
set "_H_PATH_ROOT=!_H_PATH_DIR!"
:_h_path_find_root_loop
if exist "!_H_PATH_ROOT!\AGENTS.md" goto :_h_path_find_root_done
for %%i in ("!_H_PATH_ROOT!\..") do set "_H_PATH_ROOT=%%~fi"
if "!_H_PATH_ROOT!"=="%~d0\" (
    echo ERROR: harness_path_util.bat 未找到项目根（AGENTS.md 锚点缺失）>&2
    exit /b 3
)
goto :_h_path_find_root_loop
:_h_path_find_root_done

REM --- 加载 config/paths.conf ---
set "_H_PATH_CONF_FILE=!_H_PATH_ROOT!\engineering\harness\config\paths.conf"
if not exist "!_H_PATH_CONF_FILE!" (
    echo ERROR: paths.conf 不存在: !_H_PATH_CONF_FILE!>&2
    exit /b 3
)

REM --- 解析 KEY="value" 并设置 HARNESS_PATH_<KEY>（绝对路径，反斜杠）---
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("!_H_PATH_CONF_FILE!") do (
    call :_h_path_set_var "%%a" "%%b"
)

REM --- 构造 HARNESS_PATH_PYTHONPATH（分号分隔）---
set "_H_PP_RAW=!HARNESS_PATH_PYTHON_PATH_ROOTS!"
set "HARNESS_PATH_PYTHONPATH="
:_h_py_loop
for /f "tokens=1,* delims=:" %%a in ("!_H_PP_RAW!") do (
    set "_h_pp_root=%%a"
    set "_H_PP_RAW=%%b"
)
if "!_h_pp_root!"=="" goto :_h_py_done
set "_h_pp_abs=!_H_PATH_ROOT!\!_h_pp_root!"
set "_h_pp_abs=!_h_pp_abs:/=\!"
if "!HARNESS_PATH_PYTHONPATH!"=="" (
    set "HARNESS_PATH_PYTHONPATH=!_h_pp_abs!"
) else (
    set "HARNESS_PATH_PYTHONPATH=!HARNESS_PATH_PYTHONPATH!;!_h_pp_abs!"
)
if not "!_H_PP_RAW!"=="" goto :_h_py_loop
:_h_py_done

REM --- 导出变量到调用者作用域 ---
endlocal & (
    set "REPO_ROOT=%_H_PATH_ROOT%"
    set "_HARNESS_PATH_UTIL_LOADED=1"
    REM HARNESS_PATH_* 变量通过 for 循环在 setlocal 内设置，需逐个导出
)
REM 重新设置（endlocal 会清除 delayed expansion 变量，改用直接设置）
REM 注: 上面的 endlocal & 只能导出固定变量名，动态变量需用另一种方式

REM 重新执行变量设置（不带 setlocal，直接进入调用者作用域）
set "REPO_ROOT=%_H_PATH_ROOT%" 2>nul
goto :_h_path_reload

REM --- 内部：设置单个 HARNESS_PATH_<KEY> 变量 ---
:_h_path_set_var
set "_h_sv_key=%~1"
set "_h_sv_val=%~2"
REM 去除 key 首尾空格
for /f "tokens=* delims= " %%k in ("!_h_sv_key!") do set "_h_sv_key=%%k"
if "!_h_sv_key!"=="" goto :eof
REM 去除 value 两端引号
set "_h_sv_val=!_h_sv_val:"=!"
REM 相对路径拼接 REPO_ROOT（以 / 或 \ 开头为绝对路径）
set "_h_sv_first=!_h_sv_val:~0,1!"
if not "!_h_sv_first!"=="\" if not "!_h_sv_first!"=="/" (
    set "_h_sv_val=!_H_PATH_ROOT!\!_h_sv_val!"
)
REM 正斜杠转反斜杠
set "_h_sv_val=!_h_sv_val:/=\!"
set "HARNESS_PATH_!_h_sv_key!=!_h_sv_val!"
goto :eof

REM ============================================================================
REM 重新加载阶段（endlocal 后，直接在调用者作用域设置变量）
REM ============================================================================
:_h_path_reload
REM 重新解析 paths.conf（此时无 setlocal，变量直接持久化）
set "_H_PATH_CONF_FILE=%REPO_ROOT%\engineering\harness\config\paths.conf"
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%_H_PATH_CONF_FILE%") do (
    call :_h_path_set_var_global "%%a" "%%b"
)
REM 重新构造 PYTHONPATH
set "_H_PP_RAW=%HARNESS_PATH_PYTHON_PATH_ROOTS%"
set "HARNESS_PATH_PYTHONPATH="
:_h_py_loop2
for /f "tokens=1,* delims=:" %%a in ("%_H_PP_RAW%") do (
    set "_h_pp_root=%%a"
    set "_H_PP_RAW=%%b"
)
if "%_h_pp_root%"=="" goto :_h_py_done2
set "_h_pp_abs=%REPO_ROOT%\%_h_pp_root%"
set "_h_pp_abs=%_h_pp_abs:/=\%"
if "%HARNESS_PATH_PYTHONPATH%"=="" (
    set "HARNESS_PATH_PYTHONPATH=%_h_pp_abs%"
) else (
    set "HARNESS_PATH_PYTHONPATH=%HARNESS_PATH_PYTHONPATH%;%_h_pp_abs%"
)
if not "%_H_PP_RAW%"=="" goto :_h_py_loop2
:_h_py_done2
set "_H_PATH_DIR="
set "_H_PATH_ROOT="
set "_H_PATH_CONF_FILE="
set "_H_PP_RAW="
set "_h_sv_key="
set "_h_sv_val="
set "_h_sv_first="
set "_h_pp_root="
set "_h_pp_abs="
goto :eof

REM --- 内部：全局作用域设置变量 ---
:_h_path_set_var_global
set "_h_key=%~1"
set "_h_val=%~2"
for /f "tokens=* delims= " %%k in ("%_h_key%") do set "_h_key=%%k"
if "%_h_key%"=="" goto :eof
set "_h_val=%_h_val:"=%"
set "_h_first=%_h_val:~0,1%"
if not "%_h_first%"=="\" if not "%_h_first%"=="/" (
    set "_h_val=%REPO_ROOT%\%_h_val%"
)
set "_h_val=%_h_val:/=\%"
set "HARNESS_PATH_%_h_key%=%_h_val%"
set "_h_key="
set "_h_val="
set "_h_first="
goto :eof
