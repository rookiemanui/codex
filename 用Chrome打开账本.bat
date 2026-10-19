@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py --browser chrome %*
) else (
  py -3.12 run.py --browser chrome %*
)
if errorlevel 1 (
  echo.
  echo Chrome网页版未能启动。请查看上方提示，并确认安装了Python3.12和Google Chrome。
  pause
)
