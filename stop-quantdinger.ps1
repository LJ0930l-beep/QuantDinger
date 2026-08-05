param(
    [int[]]$Ports = @(5000, 8000)
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-ListeningProcessIds([int]$Port) {
    $matches = netstat -ano | Select-String (":$Port\s+.*LISTENING\s+(\d+)$")
    foreach ($match in $matches) {
        $line = $match.ToString().Trim()
        $parts = $line -split '\s+'
        if ($parts.Count -gt 0 -and $parts[-1] -match '^\d+$') {
            [int]$parts[-1]
        }
    }
}

$candidateIds = @($Ports | ForEach-Object { Get-ListeningProcessIds $_ } | Sort-Object -Unique)
$stopped = @()
$skipped = @()

foreach ($processId in $candidateIds) {
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction Stop
        $commandLine = [string]$process.CommandLine
        if ($commandLine -notlike "*$repoRoot*") {
            $skipped += $processId
            continue
        }
        Stop-Process -Id $processId -Force -ErrorAction Stop
        $stopped += $processId
    } catch {
        $skipped += $processId
    }
}

if ($stopped.Count -eq 0) {
    Write-Output "No QuantDinger process was stopped."
} else {
    Write-Output ("Stopped QuantDinger process(es): " + ($stopped -join ', '))
}
if ($skipped.Count -gt 0) {
    Write-Output ("Skipped unrelated or inaccessible process(es): " + ($skipped -join ', '))
}
