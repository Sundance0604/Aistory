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

function Show-CurrentJob {
    try {
        $job = Invoke-RestMethod -TimeoutSec 1 "$url/api/jobs/status"
        if ($job.status -eq "running") {
            $processed = if ($null -ne $job.progress.processed) { $job.progress.processed } else { 0 }
            $total = if ($null -ne $job.progress.total) { $job.progress.total } else { "?" }
            Write-Host "[Aistory] Background job: $($job.kind), $processed / $total"
        }
        else {
            Write-Host "[Aistory] Background job: $($job.status)"
        }
    }
    catch {
        Write-Host "[Aistory] Background job status is unavailable."
    }
}

Write-Host "[Aistory] Project: $projectRoot"
Write-Host "[Aistory] Python:  $pythonExe"
Write-Host "[Aistory] URL:     $url"

if (Test-AistoryReady) {
    Write-Host "[Aistory] Status:  already running" -ForegroundColor Green
    Show-CurrentJob
    if (-not $NoOpen) {
        Start-Process $url
        Write-Host ""
        Write-Host "The existing service is still running. Press Enter to close this status window."
        Read-Host | Out-Null
    }
    exit 0
}

Write-Host "[Aistory] Status:  starting..." -ForegroundColor Yellow
$serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @("-m", "gpt_activity", "serve") -WorkingDirectory $projectRoot -NoNewWindow -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-AistoryReady) {
        $ready = $true
        break
    }
    if ($serverProcess.HasExited) {
        break
    }
}

if (-not $ready) {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
    throw "Aistory did not become ready within 30 seconds. Review the server messages above."
}

Write-Host "[Aistory] Status:  running (PID $($serverProcess.Id))" -ForegroundColor Green
Write-Host "[Aistory] Leave this window open to keep the server running."
Write-Host "[Aistory] Press Ctrl+C or close this window to stop it."
Show-CurrentJob

if (-not $NoOpen) {
    Start-Process $url
}

try {
    Wait-Process -Id $serverProcess.Id
    $serverProcess.Refresh()
    exit $serverProcess.ExitCode
}
finally {
    if (-not $serverProcess.HasExited) {
        Write-Host "[Aistory] Stopping the local server..." -ForegroundColor Yellow
        Stop-Process -Id $serverProcess.Id
    }
}
