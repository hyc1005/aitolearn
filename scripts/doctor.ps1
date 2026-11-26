Set-Location (Split-Path $PSScriptRoot -Parent)
Get-Command py,node,npm.cmd -ErrorAction SilentlyContinue | Select-Object Name,Source
if (Test-Path .venv/Scripts/python.exe) {
    & .\.venv\Scripts\python.exe -c "import struct,sqlite3; conn=sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE test USING fts5(body)'); print(struct.calcsize('P')*8, 'bit Python; SQLite FTS5 available')"
}
if (Test-Path .venv-ocr/Scripts/python.exe) {
    & .\.venv-ocr\Scripts\python.exe -c "import paddle,paddleocr; print('Paddle',paddle.__version__,'PaddleOCR',paddleocr.__version__)"
    if ($LASTEXITCODE -ne 0) { Write-Output 'OCR 环境无法导入依赖，请检查 Python 和 PaddleOCR 安装' }
} else { Write-Output 'PaddleOCR 环境待安装' }
if ($env:OPENCODE_GO_API_KEY -or (Test-Path goapi.txt)) { Write-Output '检测到 OpenCode Go 凭据配置' } else { Write-Output 'OpenCode Go 凭据待配置' }
if (Test-Path frontend/dist/index.html) { Write-Output '前端已构建' }
Write-Output "当前端口：8765（默认）"
