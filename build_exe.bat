@echo off
setlocal
cd /d "%~dp0"

echo [build] Installing runtime and build dependencies...
pip install -r requirements.txt -q
pip install pyinstaller>=6.0 -q

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [build] Packaging WeChatUIA-Tool.exe ...
python -m PyInstaller --noconfirm WeChatUIA-Tool.spec

if errorlevel 1 (
  echo [build] FAILED
  exit /b 1
)

echo.
echo [build] Done: dist\WeChatUIA-Tool.exe
echo.
echo Usage:
echo   dist\WeChatUIA-Tool.exe              ^(interactive menu^)
echo   dist\WeChatUIA-Tool.exe bootstrap    ^(6 min warmup + launch WeChat^)
echo   dist\WeChatUIA-Tool.exe probe        ^(check UI visibility^)
echo   dist\WeChatUIA-Tool.exe dump         ^(inspect control tree^)
echo   dist\WeChatUIA-Tool.exe keepalive    ^(continuous UIA client^)
echo.
pause
