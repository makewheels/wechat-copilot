# Auto-logon administrator on boot, so there's an interactive desktop for the
# relay after a restart. Cost: the password is stored in plaintext in the
# registry (DefaultPassword). Only use on this dedicated send-only box.
#   & C:\relay\set_autologon.ps1 -Pass 'xxxx'
# Undo: set AutoAdminLogon back to 0 and remove DefaultPassword.
param([string]$User = "administrator", [Parameter(Mandatory)][string]$Pass)
$k = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty $k AutoAdminLogon  "1"
Set-ItemProperty $k DefaultUserName $User
Set-ItemProperty $k DefaultPassword $Pass
Set-ItemProperty $k DefaultDomainName $env:COMPUTERNAME
"autologon enabled for $User (password stored plaintext in registry)"
