@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" call run.bat
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --windowed --onedir --name ProtonPlaywrightMailer app.py

if errorlevel 1 goto :error
copy templates.json "dist\ProtonPlaywrightMailer\templates.json" >nul
copy recipients_sample.csv "dist\ProtonPlaywrightMailer\recipients_sample.csv" >nul
copy install_browser.bat "dist\ProtonPlaywrightMailer\install_browser.bat" >nul

echo.
echo Build created under dist\ProtonPlaywrightMailer
pause
exit /b 0

:error
echo Build failed.
pause
exit /b 1
