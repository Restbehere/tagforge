@echo off
setlocal enabledelayedexpansion
rem Tag Forge production launcher — single window, no Vite dev server.
rem Builds the frontend only when sources are newer than the last build,
rem then serves the SPA + API from uvicorn alone at http://127.0.0.1:9301

cd /D "%~dp0.."
set ROOT=%CD%
set VENVPY=%ROOT%\.venv\Scripts\python.exe

if not exist "%VENVPY%" (
    echo [run] .venv missing — run scripts\dev.bat once to set up dependencies.
    pause
    exit /b 1
)

rem ---- decide whether the frontend needs a rebuild --------------------
set NEED_BUILD=0
if not exist "%ROOT%\frontend\dist\index.html" set NEED_BUILD=1
if "%1"=="--rebuild" set NEED_BUILD=1

if "%NEED_BUILD%"=="0" (
    rem newest source file newer than the built index.html? -> rebuild
    for /f "delims=" %%F in ('powershell -NoProfile -Command "$dist=(Get-Item 'frontend/dist/index.html').LastWriteTimeUtc; $newest=(Get-ChildItem 'frontend/src' -Recurse -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc; if ($newest -gt $dist) { 'stale' } else { 'fresh' }"') do set BUILDSTATE=%%F
    if "!BUILDSTATE!"=="stale" set NEED_BUILD=1
)

if "%NEED_BUILD%"=="1" (
    echo [run] building frontend...
    pushd "%ROOT%\frontend"
    call npm run build
    if errorlevel 1 (
        echo [run] frontend build failed.
        popd
        pause
        exit /b 1
    )
    popd
) else (
    echo [run] frontend build is up to date.
)

echo [run] Tag Forge on http://127.0.0.1:9301
rem Open the browser only once the port answers — uvicorn cold start takes
rem a second or two and an instant open would show "connection refused".
start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){ try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',9301); $c.Close(); Start-Process 'http://127.0.0.1:9301'; break } catch { Start-Sleep -Milliseconds 250 } }"
"%VENVPY%" -m uvicorn backend.app:app --host 127.0.0.1 --port 9301
