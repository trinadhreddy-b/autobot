@echo off
cd /d "%~dp0"

IF NOT EXIST ".env" (
    echo .env not found. Copying .env.example...
    copy .env.example .env
    echo Please edit .env with your API keys, then re-run.
    pause
    exit /b 1
)

IF NOT EXIST "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir database 2>nul
mkdir chroma_db 2>nul
mkdir data\uploads 2>nul
mkdir logs 2>nul

echo.
echo ================================================
echo   ChatBot Platform starting...
echo   Dashboard:   http://localhost:8000
echo   API Docs:    http://localhost:8000/api/docs
echo   Widget Demo: http://localhost:8000/widget-demo
echo ================================================
echo.

cd backend
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
pause
