@echo off
REM Start uvicorn bound to all interfaces so Android devices on LAN can connect.
REM The Expo app auto-detects the host IP from hostUri and hits port 8001.
cd /d "%~dp0"
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
