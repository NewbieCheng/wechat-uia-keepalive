@echo off
cd /d "%~dp0"
if exist "dist\WeChatUIA-Tool.exe" (
  "dist\WeChatUIA-Tool.exe" bootstrap %*
  exit /b %ERRORLEVEL%
)
pip install -r requirements.txt
python bootstrap_a11y.py %*
