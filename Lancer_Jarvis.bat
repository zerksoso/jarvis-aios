@echo off
REM Lance Jarvis AIOS : serveur 127.0.0.1:5230, overlay HUD, tableau de bord.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lancer.ps1" %*
if errorlevel 1 pause
