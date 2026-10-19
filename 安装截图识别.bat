@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements-ocr.lock
if errorlevel 1 goto fail
".venv\Scripts\python.exe" scripts\prepare_ocr.py %*
if errorlevel 1 goto fail
echo.
echo 截图识别组件和模型已准备好，请重新打开“启动账本.bat”。
pause
exit /b 0
:fail
echo.
echo 安装未完成。请保留上方错误信息，不要关闭证书或哈希校验。
pause
exit /b 1
