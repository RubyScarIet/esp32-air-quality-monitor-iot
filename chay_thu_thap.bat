@echo off
chcp 65001 > nul
title HE THONG THU THAP DU LIEU IOT - ESP32
echo =========================================================
echo KHOI DONG HE THONG THU THAP DU LIEU ESP32
echo =========================================================

cd /d "%~dp0"

REM Kiem tra moi truong venv trong code\venv hoac venv
if exist "code\venv\Scripts\python.exe" (
    echo [*] Dang su dung moi truong ao (code\venv)...
    "code\venv\Scripts\python.exe" "code\lay_data.py"
) else if exist "venv\Scripts\python.exe" (
    echo [*] Dang su dung moi truong ao (venv)...
    "venv\Scripts\python.exe" "code\lay_data.py"
) else (
    echo [*] Khong tim thay thu muc venv, dung python he thong...
    python "code\lay_data.py"
)

echo.
echo =========================================================
echo Chuong trinh da dung lai.
pause
