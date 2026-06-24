@echo off
chcp 65001 >nul
setlocal EnableExtensions

cd /d D:\zhuxian-catch-v0_6

REM 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到 python，请确认已配置 PATH
    pause
    exit /b 1
)

REM 杀掉旧实例（避免端口占用）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765.*LISTENING"') do (
    echo [清理] 杀掉旧进程 PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)

REM 启动服务（同步方式，便于排查）
echo.
echo =====================================================
echo   v0.6 交易录入服务 · 端口 8765
echo =====================================================
echo   [说明] 此窗口不要关闭。关闭 = 停止服务
echo   [停止] 直接关此窗口，或双击 stop_trade_form.bat
echo.

REM 后台启动并打印 PID
echo [启动] python -m v0_6.scripts.trade_form_server
start "v0.6-trade-form" python -m v0_6.scripts.trade_form_server

REM 轮询等待服务就绪
set /a waited=0
:wait_loop
    netstat -ano | findstr ":8765.*LISTENING" >nul
    if not errorlevel 1 goto ready
    set /a waited+=1
    if %waited% geq 10 (
        echo [超时] 服务未在 10s 内就绪，请检查 Python 报错
        pause
        exit /b 1
    )
    ping -n 2 127.0.0.1 >nul
    goto wait_loop

:ready
echo [就绪] 服务已起，3 秒后打开浏览器
ping -n 3 127.0.0.1 >nul
start "" http://localhost:8765/

echo [完成] 浏览器已开
echo.
endlocal
