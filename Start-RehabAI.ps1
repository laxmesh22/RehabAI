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
if (-not $env:REHABAI_POSE_KIND) { $env:REHABAI_POSE_KIND = 'opencv' }
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

function Test-OpenCVImports([string]$PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe)) { return $false }
    & $PythonExe -c "from edge.pose.opencv_mediapipe_estimator import opencv_mediapipe_available; import sys; sys.exit(0 if opencv_mediapipe_available() else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-SidecarReady([string]$Url) {
    try {
        $health = Invoke-RestMethod -Uri ($Url.TrimEnd('/') + '/health') -TimeoutSec 2
        return [bool]$health.opencv_pose_ready
    } catch {
        return $false
    }
}

function Ensure-PoseSidecar {
    $env:REHABAI_POSE_KIND = 'opencv'
    $sidecarHost = if ($env:REHABAI_POSE_SIDECAR_HOST) { $env:REHABAI_POSE_SIDECAR_HOST } else { '127.0.0.1' }
    $sidecarPort = if ($env:REHABAI_POSE_SIDECAR_PORT) { [int]$env:REHABAI_POSE_SIDECAR_PORT } else { 8091 }
    $defaultUrl = "http://${sidecarHost}:${sidecarPort}"

    # If a URL is already set and healthy, keep it (local sidecar or remote GPU).
    if ($env:REHABAI_PHONE_INFERENCE_URL -and (Test-SidecarReady $env:REHABAI_PHONE_INFERENCE_URL)) {
        Write-Host "OpenCV pose ready via $($env:REHABAI_PHONE_INFERENCE_URL)"
        return
    }

    $posePython = $null
    $localVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-OpenCVImports $localVenv) { $posePython = $localVenv }
    if (-not $posePython) {
        $ensured = & (Join-Path $PSScriptRoot 'scripts\Ensure-OpenCVPose.ps1') -RepoRoot $PSScriptRoot 2>$null
        if ($ensured -and (Test-OpenCVImports ([string]$ensured))) { $posePython = [string]$ensured }
        elseif (Test-OpenCVImports $localVenv) { $posePython = $localVenv }
    }
    if (-not $posePython) {
        Write-Warning 'OpenCV MediaPipe pose unavailable (need Python 3.12 + mediapipe from Shoulder Tracker wheels). Phone capture stays off; simulation still works.'
        Remove-Item Env:REHABAI_PHONE_INFERENCE_URL -ErrorAction SilentlyContinue
        return
    }

    # Prefer in-process when the API interpreter itself can import mediapipe.
    if ((Test-OpenCVImports $python)) {
        Remove-Item Env:REHABAI_PHONE_INFERENCE_URL -ErrorAction SilentlyContinue
        Write-Host "Phone OpenCV pose in-process via $python (Shoulder Tracker MediaPipe Solutions)"
        return
    }

    $env:REHABAI_POSE_SIDECAR_HOST = $sidecarHost
    $env:REHABAI_POSE_SIDECAR_PORT = "$sidecarPort"
    $env:REHABAI_PHONE_INFERENCE_URL = $defaultUrl
    if (-not (Test-SidecarReady $defaultUrl)) {
        Write-Host "Starting OpenCV pose sidecar on $defaultUrl with $posePython"
        Start-Process -FilePath $posePython -ArgumentList @('-m', 'ml.pose_sidecar') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null
    }
    $ready = $false
    for ($i = 0; $i -lt 50; $i++) {
        Start-Sleep -Milliseconds 400
        if (Test-SidecarReady $defaultUrl) { $ready = $true; break }
    }
    if ($ready) {
        Write-Host 'OpenCV pose sidecar ready (phone_rgb_2d · Shoulder Tracker landmarks · not diagnosis).'
    } else {
        Remove-Item Env:REHABAI_PHONE_INFERENCE_URL -ErrorAction SilentlyContinue
        Write-Warning 'Pose sidecar failed to become ready. Phone capture disabled for this run.'
    }
}

$python = 'python'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$projectVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
foreach ($candidate in @($projectVenv, $bundledPython, 'python')) {
    if ($candidate -eq 'python') {
        if (Get-Command python -ErrorAction SilentlyContinue) { $python = 'python'; break }
        continue
    }
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    & $candidate -c 'import uvicorn' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}
if ($python -eq 'python' -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python with the requirements installed was not found.'
}

Ensure-PoseSidecar

Write-Host "RehabAI hospital studio listening on http://${ListenAddress}:${Port}"
if ($Phone) { Write-Host 'Phone mode: open the PC LAN IP on the same trusted Wi-Fi network.' }
Write-Host 'Original patient station -> python -m backend.server  (port 8765)'
& $python -m uvicorn backend.main:app --host $ListenAddress --port $Port --no-access-log --log-level warning
