@echo off
REM PhoneReality - one-click start.
REM Opens two windows (demo app + dashboard) and then the dashboard in your browser.
REM Close the two windows (or press Ctrl+C in them) to stop everything.

cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment was not found.
    echo Follow "First-time setup" in README.md, then run this file again.
    pause
    exit /b 1
)

start "PhoneReality demo app (port 5050)" .venv\Scripts\python.exe demo_app.py
REM --server.address localhost: only this laptop can open the dashboard, not others on the same Wi-Fi
start "PhoneReality dashboard (port 8501)" .venv\Scripts\python.exe -m streamlit run app.py --server.address localhost
timeout /t 5 /nobreak >nul
start "" http://localhost:8501
