@echo off
REM Arrete Jarvis AIOS (overlay + serveur).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scriptsrreter.ps1"
