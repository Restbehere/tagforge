# Tag Forge dev launcher (PowerShell).
# Use scripts\dev.bat instead if you prefer cmd.

$ErrorActionPreference = "Stop"

$Root     = Resolve-Path (Join-Path $PSScriptRoot "..")
$Backend  = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Venv     = Join-Path $Root ".venv\Scripts\python.exe"

Push-Location $Root
try {
    if (-not (Test-Path $Venv)) {
        Write-Host "[dev] creating virtualenv at $Root\.venv ..." -ForegroundColor Cyan
        python -m venv (Join-Path $Root ".venv")
        & $Venv -m pip install --upgrade pip
        & $Venv -m pip install -r (Join-Path $Backend "requirements.txt")
    }

    if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
        Write-Host "[dev] installing frontend deps ..." -ForegroundColor Cyan
        Push-Location $Frontend
        cmd /c "npm install --no-audit --no-fund"
        Pop-Location
    }

    & $Venv -m backend.cli init-db
    if (-not (Test-Path (Join-Path $Backend "data\tag_tree.json"))) {
        & $Venv -m backend.cli seed-tag-tree
    }

    Write-Host "[dev] launching backend window (uvicorn :9301) ..." -ForegroundColor Green
    Start-Process -FilePath "cmd.exe" -ArgumentList @(
        "/k", "`"$Venv`" -m uvicorn backend.app:app --host 127.0.0.1 --port 9301 --reload"
    ) -WorkingDirectory $Root | Out-Null

    Write-Host "[dev] launching frontend window (vite :9300) ..." -ForegroundColor Green
    Start-Process -FilePath "cmd.exe" -ArgumentList @(
        "/k", "npm run dev"
    ) -WorkingDirectory $Frontend | Out-Null

    Write-Host ""
    Write-Host "Both servers are running in separate windows."
    Write-Host "  backend  : http://127.0.0.1:9301/api/health"
    Write-Host "  frontend : http://localhost:9300"
}
finally {
    Pop-Location
}
