param(
    [int]$Port
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $repoRoot 'backend_api_python'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime not found at $python. Create the repository .venv first."
}
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot 'run.py'))) {
    throw "Backend entrypoint not found at $backendRoot\run.py."
}

if ($Port -gt 0) {
    $env:PYTHON_API_PORT = [string]$Port
}

# The backend entrypoint loads backend_api_python/.env.  This helper only
# changes directory and delegates to that existing entrypoint; it does not
# enable TestNet writes or Live trading.
Push-Location $backendRoot
try {
    & $python 'run.py'
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
