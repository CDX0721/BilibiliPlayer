@echo off
rem BilibiliPlayer launcher - double click to start
cd /d "F:\Projects\BilibiliPlayer"
python -m app.main
if errorlevel 1 (
  echo.
  echo Program exited with an error. Press any key to close.
  pause >nul
)
