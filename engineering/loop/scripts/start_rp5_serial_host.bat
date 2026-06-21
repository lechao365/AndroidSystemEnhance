@echo off
REM ===========================================================================
REM start_rp5_serial_host.bat -- rp5-serial Windows Host quick launcher
REM
REM Usage:
REM   Double-click                 (default: COM5 / 115200 / 9700)
REM   CMD: start_rp5_serial_host.bat                   (default)
REM   CMD: start_rp5_serial_host.bat COM3              (custom COM port)
REM   CMD: start_rp5_serial_host.bat COM3 9600 9800    (full custom)
REM
REM Dependencies:
REM   - Python 3 on PATH (python command available)
REM   - pyserial installed (pip install pyserial)
REM   - Physical serial device connected (COM5 or custom)
REM
REM Stop: Ctrl-C
REM
REM WARNING: This file MUST use CRLF line endings (Windows), otherwise
REM          CMD parsing will fail. See engineering/loop/scripts/README.md.
REM ===========================================================================
setlocal enableextensions

REM --- Parse args (overridable via command line) ------------------------------
set "COM_PORT=%~1"
if "%COM_PORT%"=="" set "COM_PORT=COM5"

set "BAUDRATE=%~2"
if "%BAUDRATE%"=="" set "BAUDRATE=115200"

set "LISTEN_PORT=%~3"
if "%LISTEN_PORT%"=="" set "LISTEN_PORT=9700"

REM --- Locate repo root ------------------------------------------------------
REM %~dp0 expands to script dir (trailing backslash); strip it for clean path
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM Load harness path utility
set "HARNESS_PATH_UTIL=%SCRIPT_DIR%\..\..\harness\lib\bat\harness_path_util.bat"
call "%HARNESS_PATH_UTIL%"
if errorlevel 1 (
    echo ERROR: harness_path_util.bat load failed, exit code=%ERRORLEVEL%>&2
    exit /b %ERRORLEVEL%
)

set "LOG_DIR=%HARNESS_PATH_HOST_LOG_DIR%"

REM --- Set PYTHONPATH --------------------------------------------------------
set "PYTHON_LIB_DIR=%HARNESS_PATH_PYTHON_LIB_DIR%"
set "PYTHONPATH=%HARNESS_PATH_PYTHONPATH%;%PYTHON_LIB_DIR%"

REM --- Launch Host -----------------------------------------------------------
echo.
echo   rp5-serial Windows Host
echo   ========================
echo   COM Port  : %COM_PORT%
echo   Baudrate  : %BAUDRATE%
echo   Listen    : 0.0.0.0:%LISTEN_PORT%
echo   Log Dir   : %LOG_DIR%
echo   PYTHONPATH: %PYTHONPATH%
echo   ========================
echo   Press Ctrl-C to stop
echo.

python -m rp5_serial.host.server --port %COM_PORT% --baudrate %BAUDRATE% --listen-port %LISTEN_PORT% --log-dir "%LOG_DIR%"

REM Pass through exit code
exit /b %ERRORLEVEL%
