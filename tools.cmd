@echo off
rem ============================================================
rem  Subtitle tools - main menu (double click me)
rem    1 translate subtitles      2 audio/video -> subtitles
rem    3 floating overlay         4 verify translation alignment
rem ============================================================
chcp 65001 >nul
setlocal
if not defined SUBTITLE_PY set "SUBTITLE_PY=C:\Users\chr\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PY=%SUBTITLE_PY%"
set "BASE=%~dp0"
if "%BASE:~-1%"=="\" set "BASE=%BASE:~0,-1%"

if not exist "%PY%" (
  echo [x] Python not found: %PY%
  echo     Set SUBTITLE_PY to your python.exe, or edit this file.
  pause
  exit /b 2
)

set "PYTHONPATH=%BASE%\pylibs;%BASE%\patches"
set "PYTHONIOENCODING=utf-8"
set "TEMP=%BASE%\tmp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"

title Subtitle Tools
"%PY%" "%BASE%\scripts\launcher.py" %*
if errorlevel 1 pause
endlocal
