param(
    [switch]$Phone,
    [string]$ListenAddress = '',
    [int]$Port = 0
)
Set-Location -LiteralPath $PSScriptRoot
$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}
if (-not $env:REHABAI_SOURCE) { $env:REHABAI_SOURCE = 'simulation' }
if (-not $env:JWT_SECRET) {
    $random = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($random) } finally { $rng.Dispose() }
    $env:JWT_SECRET = [Convert]::ToBase64String($random)
}
if (-not $ListenAddress) {
    $ListenAddress = if ($Phone) { '0.0.0.0' } elseif ($env:REHABAI_HOST) { $env:REHABAI_HOST } else { '127.0.0.1' }
}
if ($Port -le 0) { $Port = if ($env:REHABAI_PORT) { [int]$env:REHABAI_PORT } else { 8000 } }
$python = 'python'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython -c 'import uvicorn' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $bundledPython }
}
if ($python -eq 'python' -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python with the requirements installed was not found.'
}
Write-Host "RehabAI hospital studio listening on http://${ListenAddress}:${Port}"
if ($Phone) { Write-Host 'Phone mode: open the PC LAN IP on the same trusted Wi-Fi network.' }
Write-Host 'Original patient station -> python -m backend.server  (port 8765)'
& $python -m uvicorn backend.main:app --host $ListenAddress --port $Port --no-access-log
