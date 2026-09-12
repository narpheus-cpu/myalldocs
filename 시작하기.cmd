@echo off
title Book Indexer Setup
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\beginner-setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the message above, then run this file again.
  pause
)
