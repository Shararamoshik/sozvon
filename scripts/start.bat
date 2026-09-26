@echo off
cd /d "%~dp0.."
if exist "dist-v02\Sozvon\Sozvon.exe" (
  "dist-v02\Sozvon\Sozvon.exe"
) else if exist "dist\Sozvon\Sozvon.exe" (
  "dist\Sozvon\Sozvon.exe"
) else (
  ".venv\Scripts\python.exe" -m sozvon
)
if errorlevel 1 pause
