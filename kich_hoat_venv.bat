@echo off
cd /d "%~dp0"
if exist "code\venv\Scripts\activate.bat" (
    call "code\venv\Scripts\activate.bat"
) else (
    call "venv\Scripts\activate.bat"
)
cmd /k

