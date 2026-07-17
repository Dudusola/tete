@echo off
REM Daily pipeline - scheduled by Windows Task Scheduler
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"
call venv\Scripts\activate.bat
REM UTF-8 so emoji in logs can't crash python on a cp1252 console
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python run_daily.py >> data\scheduler.log 2>&1
