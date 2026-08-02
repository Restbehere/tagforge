@echo off
REM Tag Forge dev launcher.
REM Bootstraps the venv + node_modules + DB, then opens two terminal windows:
REM   * backend  : uvicorn on 127.0.0.1:7860
REM   * frontend : vite    on http://localhost:5173

setlocal

set "ROOT=%~dp0.."
pushd "%ROOT%" || (echo [dev] cannot enter project root & exit /b 1)

set "VENV=%ROOT%\.venv\Scripts\python.exe"
set "BACKEND_PORT=9301"
set "FRONTEND_PORT=9300"

REM Kill any orphan dev servers from a previous run so the ports are free.
REM (cmd /k windows can leave child processes alive when closed via the X button.)
echo [dev] freeing ports %BACKEND_PORT% / %FRONTEND_PORT% if held by orphan dev servers ...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%BACKEND_PORT% :%FRONTEND_PORT%"') do (
    taskkill /PID %%P /F >NUL 2>&1
)

if not exist "%VENV%" (
    echo [dev] creating virtualenv at %ROOT%\.venv ...
    python -m venv "%ROOT%\.venv" || goto :fail
    "%VENV%" -m pip install --upgrade pip || goto :fail
    "%VENV%" -m pip install -r "%ROOT%\backend\requirements.txt" || goto :fail
)

if not exist "%ROOT%\frontend\node_modules" (
    echo [dev] installing frontend deps ...
    pushd "%ROOT%\frontend"
    call npm install --no-audit --no-fund || (popd & goto :fail)
    popd
)

echo [dev] initialising sqlite schema ...
"%VENV%" -m backend.cli init-db || goto :fail

if not exist "%ROOT%\backend\data\tag_tree.json" (
    echo [dev] downloading tag_tree.json ...
    "%VENV%" -m backend.cli seed-tag-tree || goto :fail
)

echo.
echo [dev] launching backend window  (uvicorn :%BACKEND_PORT%) ...
start "Tag Forge backend"  cmd /k ""%VENV%" -m uvicorn backend.app:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo [dev] launching frontend window (vite :%FRONTEND_PORT%) ...
start "Tag Forge frontend" cmd /k "cd /d "%ROOT%\frontend" && npm run dev"

echo.
echo Both servers are running in separate windows.
echo   backend  : http://127.0.0.1:%BACKEND_PORT%/api/health
echo   frontend : http://localhost:%FRONTEND_PORT%
echo Close those windows (or Ctrl-C inside them) to stop.
echo.

popd
endlocal
exit /b 0

:fail
echo.
echo [dev] startup failed.
popd
endlocal
exit /b 1
