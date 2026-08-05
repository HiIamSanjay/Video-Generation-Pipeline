@echo off
rem Run a pipeline script with ComfyUI's embedded Python.
rem The `python` on PATH is a broken WindowsApps stub, so scripts must use this.
rem
rem Script names resolve against pipeline\ first, so you can type the bare name
rem from the project root:
rem     .\py make_ad.py --item vegetables --percent 20
rem     .\py serve.py
rem     .\py scene.py --selftest
rem A path that already exists is used as given, so this still works:
rem     .\py pipeline\make_ad.py --dry-run
rem
rem Override the interpreter by setting COMFY_PYTHON.

setlocal
if defined COMFY_PYTHON (
    set "PY=%COMFY_PYTHON%"
) else (
    set "PY=C:\Users\User\Downloads\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\python_embeded\python.exe"
)

if not exist "%PY%" (
    echo Embedded Python not found at:
    echo     %PY%
    echo.
    echo Set COMFY_PYTHON to your ComfyUI python_embeded\python.exe, e.g.
    echo     $env:COMFY_PYTHON = "D:\ComfyUI\python_embeded\python.exe"
    exit /b 1
)

rem No arguments: show what is available.
if "%~1"=="" (
    echo Usage: .\py ^<script.py^> [args]
    echo.
    echo Available scripts in pipeline\:
    for %%f in ("%~dp0pipeline\*.py") do echo     %%~nxf
    exit /b 1
)

rem Resolve the script: as given if it exists, else under pipeline\.
set "SCRIPT=%~1"
if not exist "%SCRIPT%" (
    if exist "%~dp0pipeline\%~1" set "SCRIPT=%~dp0pipeline\%~1"
)

if not exist "%SCRIPT%" (
    echo Script not found: %~1
    echo Looked in the current directory and in %~dp0pipeline\
    exit /b 1
)

rem Everything after the script name, passed through verbatim so quoted
rem arguments survive. SHIFT is not usable here: it moves %0 as well, which
rem would re-append the script name.
set "ARGS="
for /f "tokens=1,*" %%a in ("%*") do set "ARGS=%%b"

"%PY%" "%SCRIPT%" %ARGS%
