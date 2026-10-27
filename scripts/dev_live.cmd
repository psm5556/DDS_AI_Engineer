@echo off
rem Dev run (server + PC Agent) in its own console window. Double-click, or: scripts\dev_live.cmd [demo^|claude^|gemini^|codex]
rem Close the window to stop. Web app: http://127.0.0.1:7014 (admin / PIN 123456)
rem Keep this file ASCII only - cmd.exe misreads UTF-8 Korean text in batch files.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set AGENT=%1
if "%AGENT%"=="" set AGENT=demo
title DDS AI Engineer - dev_live (%AGENT%) - close this window to stop
.venv\Scripts\python scripts\dev_live.py --data data\dev --agent %AGENT%
