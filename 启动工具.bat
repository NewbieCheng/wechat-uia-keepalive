@echo off
cd /d "%~dp0"
if exist "WeChatUIA-Tool.exe" (
  start "" "WeChatUIA-Tool.exe"
  exit /b 0
)
if exist "dist\WeChatUIA-Tool.exe" (
  start "" "dist\WeChatUIA-Tool.exe"
  exit /b 0
)
echo 未找到 WeChatUIA-Tool.exe
echo 请从 GitHub Releases 下载，或运行 build_exe.bat 自行打包。
pause
