param([int]$Port = 8010)
$ErrorActionPreference = 'Stop'
$contextPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $contextPython)) {
    throw 'Python environment not found. Follow the setup steps in README.md first.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $contextPython -m uvicorn context_protocol.app:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
