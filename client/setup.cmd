@echo off
rem CARL client setup on Windows: runs carl_setup.py next to this file (carl_client\app\setup.py; setup.cmd --help
rem shows its options). It looks for Python 3: the py launcher (py -3), else a python.exe on PATH that is not the
rem Microsoft Store's alias (under WindowsApps: it opens the Store). Without one, it names python.org in one sentence.
rem carl_setup.py checks the version (3.9 or newer) itself.
setlocal
set "CARL_PY="
py -3 -c "import sys" >NUL 2>&1 && set "CARL_PY=py -3"
if not defined CARL_PY for /f "delims=" %%P in ('where python.exe 2^>NUL') do if not defined CARL_PY call :real "%%P"
if not defined CARL_PY (
  echo error: the CARL client setup needs Python 3.9 or newer: install it from python.org, then run setup.cmd again.
  exit /b 1
)
%CARL_PY% "%~dp0carl_setup.py" %*
exit /b %ERRORLEVEL%

:real
rem %1 is a python.exe on PATH: taken when it is not the Store's alias and it runs.
set "CARL_TRY=%~1"
if /i not "%CARL_TRY:\WindowsApps\=%"=="%CARL_TRY%" exit /b 0
"%CARL_TRY%" -c "import sys" >NUL 2>&1 && set CARL_PY="%CARL_TRY%"
exit /b 0
