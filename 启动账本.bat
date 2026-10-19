@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py %*
) else (
  py -3.12 run.py %*
)
if errorlevel 1 (
  echo.
  echo 启动未完成。请先安装 Python 3.12，并查看上方提示。
  echo 可以把整个文件夹放在 D 盘；账本默认保存到同目录的 data 文件夹。
  pause
)
