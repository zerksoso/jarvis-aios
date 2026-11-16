@echo off
REM Lance Jarvis AIOS : assistant voix au 1er démarrage, serveur 127.0.0.1:5230, overlay HUD, tableau de bord.
cd /d "%~dp0"

set "JARVIS_DATA_DIR=%CD%"
if exist "%~dp0installed.flag" set "JARVIS_DATA_DIR=%USERPROFILE%\JarvisAIOS"
if defined JARVIS_ROOT set "JARVIS_DATA_DIR=%JARVIS_ROOT%"

set "JARVIS_PY=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0python\python.exe" set "JARVIS_PY=%~dp0python\python.exe"

if not exist "%JARVIS_DATA_DIR%\runtime\voix_configuree.flag" (
  if exist "%JARVIS_PY%" (
    if exist "%~dp0setup_voice.py" (
      "%JARVIS_PY%" "%~dp0setup_voice.py"
    )
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lancer.ps1" %*
if errorlevel 1 pause
