param([int]$Port = 8765, [switch]$KeywordOnly)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = (Resolve-Path .venv/Scripts/python.exe).Path
if (!(Test-Path frontend/dist/index.html)) { throw '请先运行 setup.ps1 构建前端' }
New-Item -ItemType Directory var -Force | Out-Null
if (Test-Path var/processes.json) {
    $previous = Get-Content var/processes.json -Raw | ConvertFrom-Json
    $active = @($previous.entries | Where-Object {
        $candidate = Get-Process -Id $_.pid -ErrorAction SilentlyContinue
        $candidate -and $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $_.started
    })
    if ($active.Count -eq 2) { Write-Output "http://127.0.0.1:$($previous.port)"; return }
    if ($active.Count) { & .\scripts\stop.ps1 }
}
if ($KeywordOnly) { $env:APP_KEYWORD_ONLY = '1' }
$worker = Start-Process -FilePath $python -ArgumentList '-m','backend.jobs' -WindowStyle Hidden -PassThru -RedirectStandardOutput var/worker.log -RedirectStandardError var/worker-error.log
$api = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$Port" -WindowStyle Hidden -PassThru -RedirectStandardOutput var/api.log -RedirectStandardError var/api-error.log
@{ port=$Port; entries=@($worker,$api | ForEach-Object { @{pid=$_.Id; started=$_.StartTime.ToUniversalTime().Ticks.ToString()} }) } | ConvertTo-Json | Set-Content var/processes.json
$url = "http://127.0.0.1:$Port"
for ($i=0; $i -lt 30; $i++) {
    if ($worker.HasExited -or $api.HasExited) { throw '启动失败，请查看 var 中的错误日志' }
    try { Invoke-RestMethod "$url/api/health" -TimeoutSec 1 | Out-Null; Write-Output $url; return } catch { Start-Sleep -Milliseconds 400 }
}
throw '服务启动超时，请查看 var 中的日志'
