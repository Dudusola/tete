@echo off
REM Starts the FastAPI prediction API on port 8080
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"
call venv\Scripts\activate.bat
REM UTF-8 so emoji in logs can't crash python on a cp1252 console
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -m uvicorn api.server:app --port 8080 >> data\api.log 2>&1
