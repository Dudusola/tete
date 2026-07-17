@echo off
REM Starts the Telegram bot - scheduled to run at login
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"
call venv\Scripts\activate.bat
REM UTF-8 so emoji in logs can't crash python on a cp1252 console
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python bot\telegram_bot.py >> data\bot.log 2>&1
