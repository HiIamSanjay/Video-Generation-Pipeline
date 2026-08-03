@echo off
rem Run a script with ComfyUI's embedded Python.
rem The `python` on PATH is a broken WindowsApps stub, so scripts must use this.
rem
rem Usage (PowerShell needs the .\ prefix):
rem     .\py scene.py --selftest
rem     .\py comfy_client.py --stats
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

"%PY%" %*
