# Ensure a local Python 3.12 venv can run OpenCV + MediaPipe pose (no .task/.pt file).
# Uses wheels from vendor/shoulder_tracker/.wheels and companion packages from the
# extracted Shoulder Tracker site-packages when corporate SSL blocks PyPI.
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepoRoot

function Test-Ready([string]$PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe)) { return $false }
    & $PythonExe -c "from edge.phone_capture import phone_pose_available; import sys; sys.exit(0 if phone_pose_available() else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (Test-Ready $venvPython) {
    Write-Output $venvPython
    return
}

$bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $bundled)) {
    throw 'Python 3.12 runtime not found (expected Codex bundled python). Install Python 3.12 and recreate .venv.'
}

$wheels = Join-Path $RepoRoot 'vendor\shoulder_tracker\.wheels'
if (-not (Test-Path -LiteralPath (Join-Path $wheels 'mediapipe-0.10.21-cp312-cp312-win_amd64.whl'))) {
    $zip = Get-ChildItem -LiteralPath $RepoRoot -Filter 'SHOULDER TRACKER*.zip' | Select-Object -First 1
    if (-not $zip) { throw 'Missing vendor/shoulder_tracker/.wheels and SHOULDER TRACKER*.zip' }
    New-Item -ItemType Directory -Force -Path $wheels | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip.FullName)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -like 'SHOULDER TRACKER/.wheels/*.whl' -and $entry.Name) {
                $dest = Join-Path $wheels $entry.Name
                $in = $entry.Open(); $out = [IO.File]::Create($dest)
                try { $in.CopyTo($out) } finally { $out.Dispose(); $in.Dispose() }
            }
        }
    } finally { $archive.Dispose() }
}

# Need Shoulder Tracker site-packages for matplotlib/Pillow (not always in .wheels).
$shoulderSite = Join-Path $RepoRoot 'vendor\shoulder_tracker\.venv\Lib\site-packages'
if (-not (Test-Path -LiteralPath (Join-Path $shoulderSite 'matplotlib'))) {
    Write-Host 'Extracting Shoulder Tracker site-packages for MediaPipe companions...'
    $zip = Get-ChildItem -LiteralPath $RepoRoot -Filter 'SHOULDER TRACKER*.zip' | Select-Object -First 1
    if (-not $zip) { throw 'Need SHOULDER TRACKER*.zip to copy matplotlib companions' }
    $staging = Join-Path $RepoRoot 'vendor\shoulder_tracker\_extract'
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip.FullName)
    try {
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith('SHOULDER TRACKER/.venv/Lib/site-packages/')) { continue }
            $rel = $entry.FullName.Substring('SHOULDER TRACKER/.venv/Lib/site-packages/'.Length)
            if ([string]::IsNullOrWhiteSpace($rel)) { continue }
            $target = Join-Path $staging $rel
            if ($entry.FullName.EndsWith('/')) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
                continue
            }
            $parent = Split-Path -Parent $target
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            $in = $entry.Open(); $out = [IO.File]::Create($target)
            try { $in.CopyTo($out) } finally { $out.Dispose(); $in.Dispose() }
        }
    } finally { $archive.Dispose() }
    $destSite = Join-Path $RepoRoot 'vendor\shoulder_tracker\.venv\Lib\site-packages'
    New-Item -ItemType Directory -Force -Path (Split-Path $destSite) | Out-Null
    if (Test-Path $destSite) { Remove-Item -Recurse -Force $destSite }
    Move-Item $staging $destSite
    $shoulderSite = $destSite
}

if (-not (Test-Path $venvPython)) {
    & $bundled -m venv (Join-Path $RepoRoot '.venv')
}

Write-Host 'Installing OpenCV/MediaPipe wheels into .venv (offline, --no-deps)...'
& $venvPython -m pip install --no-index --find-links=$wheels --no-deps mediapipe==0.10.21 opencv-python==4.10.0.84
& $venvPython -m pip install --no-index --find-links=$wheels numpy==1.26.4 absl-py attrs flatbuffers protobuf

$dst = Join-Path $RepoRoot '.venv\Lib\site-packages'
$copyNames = @(
  'matplotlib','matplotlib-3.11.1.dist-info','mpl_toolkits','contourpy','contourpy-1.3.3.dist-info',
  'cycler','cycler-0.12.1.dist-info','fontTools','fonttools-4.65.0.dist-info',
  'kiwisolver','kiwisolver-1.5.1.dist-info','packaging','packaging-26.3.dist-info',
  'PIL','pillow-12.3.0.dist-info','pyparsing','pyparsing-3.3.2.dist-info',
  'dateutil','python_dateutil-2.9.0.post0.dist-info','six.py','six-1.17.0.dist-info',
  'sounddevice.py','_sounddevice.py','sounddevice-0.5.6.dist-info',
  'cffi','cffi-2.1.1.dist-info','_cffi_backend.cp312-win_amd64.pyd','pycparser','pycparser-3.0.dist-info'
)
foreach ($name in $copyNames) {
    $from = Join-Path $shoulderSite $name
    $to = Join-Path $dst $name
    if (-not (Test-Path $from)) { continue }
    if (Test-Path $to) { Remove-Item -Recurse -Force $to }
    Copy-Item -Recurse -Force $from $to
}

if (-not (Test-Ready $venvPython)) {
    throw 'OpenCV pose bootstrap finished but phone_pose_available() is still false'
}
Write-Host "OpenCV pose ready: $venvPython"
Write-Output $venvPython
