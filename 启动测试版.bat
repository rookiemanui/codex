@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py --test-mode --browser chrome %*
) else (
  py -3.12 run.py --test-mode --browser chrome %*
)
if errorlevel 1 (
  echo.
  echo 测试版启动失败。请检查上方提示，确认已安装 Python 3.12 和 Google Chrome。
  echo 请先完整解压软件，不要只打开HTML文件。测试数据保存在 test-data 文件夹。
  pause
)
