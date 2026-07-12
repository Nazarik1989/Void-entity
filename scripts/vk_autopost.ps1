$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$publisher = Join-Path $projectDir "vk_browser_publisher.py"
$logDir = Join-Path $projectDir "logs"
$logFile = Join-Path $logDir "vk-autopost.log"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Set-Location -LiteralPath $projectDir
$env:VK_BROWSER_PROFILE_DIR = Join-Path $projectDir "data\vk_autopost_profile"
$env:VK_BROWSER_HEADLESS = "true"

$startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$startedAt] VK autopost started" | Add-Content -LiteralPath $logFile -Encoding utf8

try {
    & $python $publisher publish-scheduled *>&1 |
        Tee-Object -FilePath $logFile -Append
    if ($LASTEXITCODE -ne 0) {
        throw "VK publisher exited with code $LASTEXITCODE"
    }
    $finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$finishedAt] VK autopost completed" | Add-Content -LiteralPath $logFile -Encoding utf8
}
catch {
    $failedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$failedAt] VK autopost failed: $($_.Exception.Message)" |
        Add-Content -LiteralPath $logFile -Encoding utf8
    throw
}
