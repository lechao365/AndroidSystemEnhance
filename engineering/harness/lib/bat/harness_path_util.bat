@echo off
REM ============================================================================
REM harness_path_util.bat -- Windows batch unified path utility
REM Rule: engineering/harness/rules/path-management.md (PATH-001)
REM
REM Design:
REM   bat has no function return value, uses "load-and-set-variables" pattern.
REM   After calling this file, all HARNESS_PATH_* variables are available
REM   in the caller scope.
REM
REM Usage (called by other bat scripts):
REM   set "SCRIPT_DIR=%~dp0"
REM   set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
REM   call "%SCRIPT_DIR%\..\..\lib\bat\harness_path_util.bat"
REM   echo %REPO_ROOT%
REM   echo %HARNESS_PATH_HOST_LOG_DIR%
REM   echo %HARNESS_PATH_PYTHONPATH%
REM
REM Variables:
REM   REPO_ROOT                 -- repo root absolute path
REM   HARNESS_PATH_KEY          -- absolute path for KEY in harness-paths.conf
REM   HARNESS_PATH_PYTHONPATH   -- assembled PYTHONPATH, semicolon-separated
REM ============================================================================

setlocal enabledelayedexpansion

REM --- Locate REPO_ROOT by searching upwards for AGENTS.md anchor ---
set "_H_PATH_DIR=%~dp0"
set "_H_PATH_DIR=!_H_PATH_DIR:~0,-1!"
set "_H_PATH_ROOT=!_H_PATH_DIR!"
:_h_path_find_root_loop
if exist "!_H_PATH_ROOT!\AGENTS.md" goto :_h_path_find_root_done
for %%i in ("!_H_PATH_ROOT!\..") do set "_H_PATH_ROOT=%%~fi"
if "!_H_PATH_ROOT!"=="%~d0\" (
    echo ERROR: harness_path_util.bat cannot find repo root, AGENTS.md anchor missing>&2
    exit /b 3
)
goto :_h_path_find_root_loop
:_h_path_find_root_done

REM --- Load config/harness-paths.conf ---
set "_H_PATH_CONF_FILE=!_H_PATH_ROOT!\engineering\harness\config\harness-paths.conf"
if not exist "!_H_PATH_CONF_FILE!" (
    echo ERROR: harness-paths.conf not found: !_H_PATH_CONF_FILE!>&2
    exit /b 3
)

REM --- Parse KEY="value" lines and set HARNESS_PATH_KEY variables ---
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("!_H_PATH_CONF_FILE!") do (
    call :_h_path_set_var "%%a" "%%b"
)

REM --- Build HARNESS_PATH_PYTHONPATH, semicolon-separated ---
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

REM --- Export REPO_ROOT to caller scope, then reload all vars globally ---
endlocal & (
    set "REPO_ROOT=%_H_PATH_ROOT%"
    set "_HARNESS_PATH_UTIL_LOADED=1"
)

REM --- Reload phase: re-parse config in caller scope, no setlocal ---
goto :_h_path_reload

REM --- Internal: set single HARNESS_PATH_KEY variable, local scope ---
:_h_path_set_var
set "_h_sv_key=%~1"
set "_h_sv_val=%~2"
REM Trim leading spaces from key
for /f "tokens=* delims= " %%k in ("!_h_sv_key!") do set "_h_sv_key=%%k"
if "!_h_sv_key!"=="" goto :eof
REM Strip all quotes from value
set "_h_sv_val=!_h_sv_val:"=!"
REM Expand $HOME to %USERPROFILE% (bat has no POSIX $HOME expansion)
REM Only ENV_* keys carry shell-style $HOME references in harness-paths.conf
set "_h_sv_val=!_h_sv_val:$HOME=%USERPROFILE%!"
REM Prepend REPO_ROOT if relative path, except for PYTHON_PATH_ROOTS
if /i not "!_h_sv_key!"=="PYTHON_PATH_ROOTS" (
    set "_h_sv_first=!_h_sv_val:~0,1!"
    if not "!_h_sv_first!"=="\" if not "!_h_sv_first!"=="/" (
        REM Skip prepend if value references a Windows env var (e.g. %USERPROFILE%)
        if not "!_h_sv_first!"=="%%" (
            set "_h_sv_val=!_H_PATH_ROOT!\!_h_sv_val!"
        )
    )
)
REM Convert forward slashes to backslashes
set "_h_sv_val=!_h_sv_val:/=\!"
set "HARNESS_PATH_!_h_sv_key!=!_h_sv_val!"
goto :eof

REM ============================================================================
REM Reload phase: after endlocal, set variables directly in caller scope
REM ============================================================================
:_h_path_reload
set "_H_PATH_CONF_FILE=%REPO_ROOT%\engineering\harness\config\harness-paths.conf"
if not exist "%_H_PATH_CONF_FILE%" (
    echo ERROR: _h_path_reload: harness-paths.conf not found: %_H_PATH_CONF_FILE%>&2
    exit /b 3
)
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%_H_PATH_CONF_FILE%") do (
    call :_h_path_set_var_global "%%a" "%%b"
)
REM Rebuild PYTHONPATH
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
REM Cleanup temp variables
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

REM --- Internal: set single HARNESS_PATH_KEY variable, global scope ---
:_h_path_set_var_global
set "_h_key=%~1"
set "_h_val=%~2"
for /f "tokens=* delims= " %%k in ("%_h_key%") do set "_h_key=%%k"
if "%_h_key%"=="" goto :eof
set "_h_val=%_h_val:"=%"
REM Expand $HOME to %USERPROFILE% (bat has no POSIX $HOME expansion)
set "_h_val=%_h_val:$HOME=%USERPROFILE%%"
REM Prepend REPO_ROOT if relative path, except for PYTHON_PATH_ROOTS
if /i not "%_h_key%"=="PYTHON_PATH_ROOTS" (
    set "_h_first=%_h_val:~0,1%"
    if not "%_h_first%"=="\" if not "%_h_first%"=="/" (
        REM Skip prepend if value references a Windows env var (e.g. %USERPROFILE%)
        if not "%_h_first%"=="%%" (
            set "_h_val=%REPO_ROOT%\%_h_val%"
        )
    )
)
set "_h_val=%_h_val:/=\%"
set "HARNESS_PATH_%_h_key%=%_h_val%"
set "_h_key="
set "_h_val="
set "_h_first="
goto :eof
