param([string]$Python = "", [switch]$SkipOcr, [switch]$KeywordOnly)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Split-Path $PSScriptRoot -Parent)
function Invoke-Native($Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令退出码 $LASTEXITCODE，详见上方输出。" }
}
if ($Python) { Invoke-Native $Python @('-m','venv','.venv') }
else { Invoke-Native 'py' @('-3.11','-m','venv','.venv') }
$appPython = '.\.venv\Scripts\python.exe'
if (!$KeywordOnly) {
    Invoke-Native $appPython @('-m','pip','install','torch','--index-url','https://download.pytorch.org/whl/cpu')
}
$requirements = if (Test-Path requirements.lock.txt) { 'requirements.lock.txt' } else { 'requirements.txt' }
if ($KeywordOnly) {
    $runtime = & $appPython -c 'import sys; print(f"{sys.platform}-{sys.version_info.major}.{sys.version_info.minor}-{sys.maxsize.bit_length()}")'
    if ($runtime -eq 'win32-3.12-63') {
        $requirements = 'requirements-keyword-win-py312.txt'
    } else {
        Get-Content requirements.txt | Where-Object { $_ -notmatch '^sentence-transformers' } | Set-Content -Encoding ASCII .venv/requirements-keyword.txt
        $requirements = '.venv/requirements-keyword.txt'
    }
}
Invoke-Native $appPython @('-m','pip','install','-r',$requirements)
if (!$KeywordOnly) {
    Invoke-Native $appPython @('-c', "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').encode(['一元一次方程'])")
}
if (!$SkipOcr) {
    if ($Python) { Invoke-Native $Python @('-m','venv','.venv-ocr') }
    else { Invoke-Native 'py' @('-3.11','-m','venv','.venv-ocr') }
    $ocr = '.\.venv-ocr\Scripts\python.exe'
    Invoke-Native $ocr @('-m','pip','install','paddlepaddle==3.2.0','-i','https://www.paddlepaddle.org.cn/packages/stable/cpu/')
    Invoke-Native $ocr @('-m','pip','install','paddleocr[doc-parser]>=3.2,<4')
    Invoke-Native $ocr @('-c', 'import paddle; paddle.utils.run_check()')
    Write-Output '正在准备 PaddleOCR 模型。首次下载可能需要几分钟。'
    Invoke-Native $ocr @('-m','ocr_worker','--warmup')
}
Push-Location frontend
try {
    Invoke-Native 'npm.cmd' @('ci','--no-audit','--no-fund')
    Invoke-Native 'npm.cmd' @('run','build')
} finally { Pop-Location }
Invoke-Native $appPython @('-m','backend.store')
& .\scripts\doctor.ps1
