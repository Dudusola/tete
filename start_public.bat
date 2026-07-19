@echo off
REM ── One command to put PotentialPro on the internet for testers ──────────────
REM The API now serves the web app too (single origin), so ONE tunnel exposes the
REM whole thing. config.js is blank => the site calls the API at its own origin,
REM so a changing quick-tunnel URL needs NO edits. Just share the URL cloudflared
REM prints. (Named tunnel w/ a fixed URL = later, once you have a domain.)
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"

echo Starting API (serves the web app too) on port 8080...
start "PotentialPro API" /min cmd /c start_api.bat

echo Starting Telegram bot...
start "PotentialPro Bot" /min cmd /c start_bot.bat

echo Waiting for the API to come up...
:waitloop
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "try { (Invoke-WebRequest http://127.0.0.1:8080/healthz -UseBasicParsing -TimeoutSec 2) | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 goto waitloop

echo.
echo API is up. Opening public tunnel — SHARE the https URL it prints below.
echo (Note: --protocol http2 because this network blocks the QUIC/UDP path;
echo  127.0.0.1 not localhost so cloudflared hits IPv4 where uvicorn listens.)
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8080 --protocol http2 --no-autoupdate
