@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\remote_trial.py %*
) else (
  py -3.12 scripts\remote_trial.py %*
)
if errorlevel 1 (
  echo.
  echo 外网试用未完成，请查看上面的说明；本地账目会保留。
  pause
)
