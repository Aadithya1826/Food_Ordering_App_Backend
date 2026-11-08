@echo off
cd /d "%~dp0"
echo Starting FastAPI Backend Server on http://localhost:8001 ...
python -m uvicorn app.main:app --reload --port 8001 --host 0.0.0.0 || py -m uvicorn app.main:app --reload --port 8001 --host 0.0.0.0 || uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
pause
