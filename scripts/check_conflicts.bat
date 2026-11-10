@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
python src\l4d2_conflict_checker.py %*
echo.
pause
