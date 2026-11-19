@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m pip install -r requirements.txt pyinstaller pywebview
py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name wintercc --add-data "frontend;frontend" --collect-all uvicorn --collect-all fastapi --collect-all webview desktop\launcher.py
if errorlevel 1 (echo Build failed & exit /b 1)
echo Built: dist\wintercc.exe
