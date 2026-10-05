@echo off
rem ============================================================
rem  Floating subtitle overlay - always on top, transparent, click-through.
rem
rem  Double click  -> pick a subtitle file (optional audio), then launch
rem  Command line  -> overlay.cmd "movie.bilingual.srt" [--audio "movie.mkv"] [--font-size 44]
rem  Self check    -> overlay.cmd --selftest "movie.bilingual.srt"
rem
rem  Requires Python 3.8+ with tkinter (comes with the official installer).
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

title Subtitle Overlay
"%PY%" "%BASE%\scripts\launcher.py" overlay %*
if errorlevel 1 pause
endlocal
