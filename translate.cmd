@echo off
rem ============================================================
rem  Subtitle translator - double click is fine now.
rem
rem  Double click  -> file picker + guided prompts
rem  Command line  -> translate.cmd "movie.ja.srt" [--outdir DIR] [--name NAME] ...
rem
rem  Use your own Python: set SUBTITLE_PY before running, or edit the
rem  default below. Python 3.10+ is enough for translating.
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

title Subtitle Translator
"%PY%" "%BASE%\scripts\launcher.py" translate %*
if errorlevel 1 pause
endlocal
