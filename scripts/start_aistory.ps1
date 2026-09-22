param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot "config.local.json"
$hostAddress = "127.0.0.1"
$port = 8765

if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.app.host) { $hostAddress = [string]$config.app.host }
    if ($config.app.port) { $port = [int]$config.app.port }
}

$browserHost = if ($hostAddress -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $hostAddress }
$url = "http://${browserHost}:${port}"
$pythonCandidates = @(
    $env:AISTORY_PYTHON,
    (Join-Path $env:USERPROFILE ".conda\envs\pavane\python.exe"),
    (Join-Path $env:USERPROFILE "miniconda3\envs\pavane\python.exe"),
    (Join-Path $env:USERPROFILE "anaconda3\envs\pavane\python.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

$pythonExe = $pythonCandidates | Select-Object -First 1
if (-not $pythonExe) {
    throw "Cannot find the pavane Python environment. Set AISTORY_PYTHON to its python.exe path."
}

function Test-AistoryReady {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "$url/api/health"
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Test-AistoryReady)) {
    Start-Process -FilePath $pythonExe -ArgumentList @("-m", "gpt_activity", "serve") -WorkingDirectory $projectRoot
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-AistoryReady) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "Aistory did not become ready within 30 seconds."
    }
}

if (-not $NoOpen) {
    Start-Process $url
}
