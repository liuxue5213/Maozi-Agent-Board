@echo off
rem WorkBuddy 常驻启动（托盘模式）：双击即用，或放 shell:startup 实现开机自启。
rem 用 dist 里的独立 exe；没有打包时回退 python -m workbuddy run。
setlocal
set DIR=%~dp0
if exist "%DIR%dist\workbuddy-agent\workbuddy-agent.exe" (
    start "" "%DIR%dist\workbuddy-agent\workbuddy-agent.exe" run --tray --no-window
) else (
    python -m workbuddy run --tray --no-window
)
endlocal
