@echo off
REM ===========================================================================
REM start_rp5_serial_host.bat -- rp5-serial Windows Host 快速启动
REM
REM 用法:
REM   双击运行                           (使用默认参数: COM5 / 115200 / 9700)
REM   CMD: start_rp5_serial_host.bat                       (使用默认参数)
REM   CMD: start_rp5_serial_host.bat COM3                   (自定义 COM 口)
REM   CMD: start_rp5_serial_host.bat COM3 9600 9800         (全参数自定义)
REM
REM 依赖:
REM   - Windows 已安装 Python 3 并注册到 PATH (python 命令可用)
REM   - pyserial 已安装 (pip install pyserial)
REM   - 物理串口设备已连接 (COM5 或命令行指定)
REM
REM 停止: Ctrl-C
REM ===========================================================================
setlocal enableextensions

REM --- 参数解析 (可被命令行覆盖) ----------------------------------------------
set "COM_PORT=%~1"
if "%COM_PORT%"=="" set "COM_PORT=COM5"

set "BAUDRATE=%~2"
if "%BAUDRATE%"=="" set "BAUDRATE=115200"

set "LISTEN_PORT=%~3"
if "%LISTEN_PORT%"=="" set "LISTEN_PORT=9700"

REM --- 定位仓库路径 -----------------------------------------------------------
REM %~dp0 展开为脚本所在目录 (含末尾反斜杠), 去掉反斜杠得到干净路径
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM provider python 根目录: engineering/loop/connection/providers/rp5-serial/python
set "PROVIDER_PYTHON=%SCRIPT_DIR%\..\connection\providers\rp5-serial\python"

REM --- 设置 PYTHONPATH -------------------------------------------------------
set "PYTHONPATH=%PROVIDER_PYTHON%"

REM --- 启动 Host -------------------------------------------------------------
echo.
echo   rp5-serial Windows Host
echo   ========================
echo   COM Port  : %COM_PORT%
echo   Baudrate  : %BAUDRATE%
echo   Listen    : 0.0.0.0:%LISTEN_PORT%
echo   PYTHONPATH: %PYTHONPATH%
echo   ========================
echo   Press Ctrl-C to stop
echo.

python -m rp5_serial.host.server --port %COM_PORT% --baudrate %BAUDRATE% --listen-port %LISTEN_PORT%

REM 退出码透传
exit /b %ERRORLEVEL%
