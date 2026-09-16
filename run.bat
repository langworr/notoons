@echo off
echo Starting Falcon ASGI with Uvicorn...
REM .\env\dev\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
.\env\dev\Scripts\python.exe -m uvicorn notoons.app.main:app --host 127.0.0.1 --port 8000 --reload

