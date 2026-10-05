@echo off
set "ENVIRONMENT=%~1"
if "%ENVIRONMENT%"=="" set "ENVIRONMENT=dev"

if /I "%ENVIRONMENT%"=="dev" set "PYTHON=.\env\dev\Scripts\python.exe"
if /I "%ENVIRONMENT%"=="prod" set "PYTHON=.\env\prod\Scripts\python.exe"

if not defined PYTHON (
	echo Usage: run.bat [dev^|prod]
	exit /b 1
)

if not exist "%PYTHON%" (
	echo Environment not found: %PYTHON%
	exit /b 1
)

echo Starting Falcon ASGI with the %ENVIRONMENT% environment...
"%PYTHON%" -m uvicorn notoons.app.main:app --host 127.0.0.1 --port 8000 --reload

