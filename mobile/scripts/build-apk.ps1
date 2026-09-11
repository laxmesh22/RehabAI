$ErrorActionPreference = 'Stop'
$mobileRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $mobileRoot

if (-not $env:JAVA_HOME) {
    $jdk17 = Get-ChildItem 'C:\Program Files\Microsoft' -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($jdk17) { $env:JAVA_HOME = $jdk17.FullName }
}
if (-not $env:JAVA_HOME -or -not (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
    throw 'JDK 17 is required. Set JAVA_HOME to an installed JDK 17 directory.'
}
$env:Path = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:Path

if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
if (-not (Test-Path -LiteralPath $env:ANDROID_HOME)) {
    throw 'Android SDK is required. Install it with Android Studio or set ANDROID_HOME.'
}

npm run cap:sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location -LiteralPath (Join-Path $mobileRoot 'android')
try {
    & .\gradlew.bat assembleDebug
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

$apk = Join-Path $mobileRoot 'android\app\build\outputs\apk\debug\app-debug.apk'
if (-not (Test-Path -LiteralPath $apk)) { throw 'Gradle completed but the debug APK was not found.' }
Get-Item -LiteralPath $apk | Select-Object FullName, Length, LastWriteTime
