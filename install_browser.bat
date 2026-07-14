@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv
    if errorlevel 1 goto :error
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Playwright Chromium was installed successfully.
pause
exit /b 0

:error
echo.
echo Browser installation failed. Review the error above.
pause
exit /b 1
