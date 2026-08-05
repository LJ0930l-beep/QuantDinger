param(
    [int]$BackendPort = 5000,
    [int]$FrontendPort = 8000
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $repoRoot 'backend_api_python'
$frontendRoot = Join-Path $repoRoot 'QuantDinger-Vue'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime not found at $python."
}
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot 'run.py'))) {
    throw "Backend entrypoint not found at $backendRoot\run.py."
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'package.json'))) {
    throw "Frontend package manifest not found at $frontendRoot\package.json."
}
if (-not $npm) {
    throw "npm.cmd was not found on PATH."
}

function Test-ListeningPort([int]$Port) {
    return [bool](netstat -ano | Select-String (":$Port\s+.*LISTENING"))
}

function Wait-ForListeningPort([int]$Port, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (Test-ListeningPort $Port)) {
        if ((Get-Date) -ge $deadline) {
            return $false
        }
        Start-Sleep -Milliseconds 250
    }
    return $true
}

if (-not (Test-ListeningPort $BackendPort)) {
    # Enable only the authenticated Gate TestNet/public market read providers.
    # Explicitly pin both write and Live flags off for this local launcher.
    $backendEnv = "`$env:PYTHON_API_PORT='$BackendPort'; `$env:QUANT_GATE_PRIVATE_READ_ENABLED='1'; `$env:QUANT_GATE_PUBLIC_MARKET_READ_ENABLED='1'; `$env:GATE_TESTNET_WRITE_ENABLED='0'; `$env:AGENT_LIVE_TRADING_ENABLED='0'; Set-Location -LiteralPath '$backendRoot'; & '$python' 'run.py'"
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', $backendEnv) -WindowStyle Hidden | Out-Null
}

if (-not (Test-ListeningPort $FrontendPort)) {
    $frontendEnv = "Set-Location -LiteralPath '$frontendRoot'; & '$npm' run dev -- --host 127.0.0.1 --port $FrontendPort"
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', $frontendEnv) -WindowStyle Hidden | Out-Null
}

if (-not (Wait-ForListeningPort $BackendPort)) {
    throw "Backend did not become ready on http://127.0.0.1:$BackendPort within 30 seconds. Check backend_api_python/logs/api.log."
}
if (-not (Wait-ForListeningPort $FrontendPort)) {
    throw "Frontend did not become ready on http://127.0.0.1:$FrontendPort within 30 seconds. Check the frontend npm process."
}

Write-Output "Backend: http://127.0.0.1:$BackendPort"
Write-Output "Frontend: http://127.0.0.1:$FrontendPort/#/quant-dashboard"
Write-Output "Both services are ready; you can close this terminal."
Write-Output "Gate private/public reads are enabled for TestNet evidence; TestNet writes and Live trading remain disabled by this helper."
