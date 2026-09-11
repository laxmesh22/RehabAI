Set-Location -LiteralPath $PSScriptRoot
$env:REHABAI_SOURCE = 'simulation'
$python = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'python'
}
Write-Host 'RehabAI hospital studio -> http://127.0.0.1:8000'
Write-Host 'Original patient station -> python -m backend.server  (port 8765)'
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
