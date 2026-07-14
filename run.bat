@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python app.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The application could not start. Review the error above.
pause
exit /b 1
