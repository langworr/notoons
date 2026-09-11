@echo off
echo Starting Falcon ASGI with Uvicorn...
.\env\dev\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload

