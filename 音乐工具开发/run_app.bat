@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [BioSound GVR] Creating local Python environment...
  python -m venv --system-site-packages .venv
)
".venv\Scripts\python.exe" -c "import streamlit, numpy, pandas" >nul 2>&1
if errorlevel 1 (
  echo [BioSound GVR] Installing dependencies for the first run...
  ".venv\Scripts\python.exe" -m pip install --no-color -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
echo [BioSound GVR] Opening local application...
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:8501'"
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless=true --browser.gatherUsageStats=false --server.port=8501
endlocal
