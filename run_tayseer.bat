@echo off
title Tayseer Agri-SaT Production Server
echo ======================================================================
echo             TAYSEER AGRI-SAT — MASTER PLATFORM LAUNCHER
echo ======================================================================
cd /d "%~dp0"
echo Working Directory: %CD%
echo Using Python 3.12: C:\Users\Youssef\AppData\Local\Programs\Python\Python312\python.exe
echo Starting Server on port 5000 (http://localhost:5000)...
echo ======================================================================

"C:\Users\Youssef\AppData\Local\Programs\Python\Python312\python.exe" run_server.py

if errorlevel 1 (
    echo.
    echo Server stopped with an error. Falling back to direct app runner...
    "C:\Users\Youssef\AppData\Local\Programs\Python\Python312\python.exe" app.py
)

pause
