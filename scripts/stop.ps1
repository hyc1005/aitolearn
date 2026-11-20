Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path var/processes.json)) { return }
$record = Get-Content var/processes.json -Raw | ConvertFrom-Json
foreach ($entry in $record.entries) {
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) {
        Stop-Process -Id $entry.pid
    }
}
Remove-Item -LiteralPath var/processes.json
