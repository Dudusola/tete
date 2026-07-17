@echo off
REM Serves the web app on http://localhost:3000 (never open it via file://)
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files\webapp"
call ..\venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -m http.server 3000 >> ..\data\web.log 2>&1
