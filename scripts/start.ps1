param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$projectPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    Write-Error 'Missing .venv. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt'
}
& $projectPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
