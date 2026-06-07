# Register relay_server.py as a scheduled task running in the INTERACTIVE session
# (so it can drive the WeChat window). Usage on server (via WinRM):
#   & C:\relay\install_task.ps1 -StartNow
param([switch]$StartNow)
$ErrorActionPreference = "Stop"

$py = "C:\relay\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { $py = "C:\relay\.venv\Scripts\python.exe" }

$action    = New-ScheduledTaskAction -Execute $py -Argument "C:\relay\relay_server.py" -WorkingDirectory "C:\relay"
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User "administrator"
$principal = New-ScheduledTaskPrincipal -UserId "administrator" -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
               -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
               -MultipleInstances IgnoreNew

Unregister-ScheduledTask -TaskName "WeChatRelay" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "WeChatRelay" -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
"registered WeChatRelay -> $py"

if ($StartNow) {
    Start-ScheduledTask -TaskName "WeChatRelay"
    Start-Sleep -Seconds 3
    $info = Get-ScheduledTask -TaskName "WeChatRelay" | Get-ScheduledTaskInfo
    "LastTaskResult=$($info.LastTaskResult) State=$((Get-ScheduledTask -TaskName WeChatRelay).State)"
}
