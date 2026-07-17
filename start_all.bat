@echo off
REM One-click start: API + web app + Telegram bot, each minimized.
REM Safe to re-run only after stopping old instances first — duplicate bots
REM fight over Telegram polling and duplicate servers fight over ports.
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"
start "PotentialPro API" /min cmd /c start_api.bat
start "PotentialPro Web" /min cmd /c start_web.bat
start "PotentialPro Bot" /min cmd /c start_bot.bat
echo API on http://localhost:8080  -  Web on http://localhost:3000
