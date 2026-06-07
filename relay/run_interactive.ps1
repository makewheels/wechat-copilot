param([Parameter(Mandatory=$true)][string]$Script,[int]$TimeoutSec=60)
$ErrorActionPreference = "Stop"
$py = "C:\relay\.venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$Script`""
$principal = New-ScheduledTaskPrincipal -UserId "administrator" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "RelayOnce" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "RelayOnce"
for ($i=0; $i -lt $TimeoutSec; $i++) {
  Start-Sleep -Seconds 1
  if ((Get-ScheduledTask -TaskName "RelayOnce").State -ne "Running") { break }
}
$info = Get-ScheduledTaskInfo -TaskName "RelayOnce"
"LastTaskResult=$($info.LastTaskResult)"
Unregister-ScheduledTask -TaskName "RelayOnce" -Confirm:$false
