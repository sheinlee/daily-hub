@echo off
rem Start the daily hub elevated (one UAC prompt), without a console window.
rem uv's .venv\Scripts\pythonw.exe is a console program, so run hub.pyw with the base
rem interpreter's pythonw.exe (the "home" line of .venv\pyvenv.cfg).
set "PYHOME="
for /f "tokens=1,* delims== " %%a in ('findstr /b /c:"home" "%~dp0.venv\pyvenv.cfg"') do set "PYHOME=%%b"
if not exist "%PYHOME%\pythonw.exe" goto fallback
start "" "%PYHOME%\pythonw.exe" -I "%~dp0hub.pyw"
goto :eof
:fallback
start "" "%~dp0.venv\Scripts\pythonw.exe" -m daily_hub
