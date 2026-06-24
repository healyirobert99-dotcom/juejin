@echo off
chcp 65001 >nul
cd /d D:\zhuxian-catch-v0_6
:loop
    netstat -ano | findstr ":8765.*LISTENING" >nul
    if errorlevel 1 (
        python -m v0_6.scripts.trade_form_server >> "D:\zhuxian-catch-v0_6\trade_form.log" 2>&1
    )
    timeout /t 30 >nul
    goto loop
