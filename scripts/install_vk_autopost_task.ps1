param(
    [int]$IntervalHours = 3,
    [string]$TaskName = "VoidVkAutopost"
)

$ErrorActionPreference = "Stop"

if ($IntervalHours -lt 1 -or $IntervalHours -gt 24) {
    throw "IntervalHours must be between 1 and 24"
}

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $PSScriptRoot "vk_autopost.ps1"
$now = Get-Date
$firstRun = Get-Date -Hour $now.Hour -Minute 0 -Second 0
do {
    $firstRun = $firstRun.AddHours(1)
} while (($firstRun.Hour % $IntervalHours) -ne 0)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcher`"" `
    -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $user `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "VOID VK autopost every $IntervalHours hours via the saved browser profile" `
    -Force | Out-Null

Write-Output "Task: $TaskName"
Write-Output "Interval: every $IntervalHours hours"
Write-Output "First run: $($firstRun.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
