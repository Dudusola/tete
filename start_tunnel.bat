@echo off
REM Exposes the local API (port 8080) to the public internet so the deployed
REM PWA can reach the laptop. Run start_all.bat FIRST (API must be on 8080).
REM Gives a https URL — put it in webapp/config.js as PP_API_BASE.
REM Requires Node (npx). For a stable URL prefer Cloudflare Tunnel (see DEPLOY.md).
cd /d "C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files"
echo Starting public tunnel to http://localhost:8080 ...
echo Copy the https URL it prints into webapp/config.js
npx localtunnel --port 8080 --subdomain potentialpro-api
