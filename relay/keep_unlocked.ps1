# Keep the interactive desktop logged-in and UNLOCKED, otherwise GUI
# automation (focus / screen grab) fails. Three reversible things:
#   1) redirect administrator's disconnected session back to the physical console (unlocks it)
#   2) disable monitor/standby/hibernate timeouts
#   3) disable screensaver
# NOTE: after a reboot you still need administrator auto-logon to get an
# interactive session at all -- see set_autologon.ps1.
$ErrorActionPreference = "Continue"

# 1) tscon to console
$id = $null
foreach ($l in (quser 2>$null)) {
    if ($l -match 'administrator' -and $l -match '\s(\d+)\s') { $id = $matches[1]; break }
}
if ($id) {
    try { tscon $id /dest:console; "tscon $id -> console" }
    catch { "tscon failed: $_" }
} else {
    "no administrator session found (not logged in?)"
}

# 2) power: never time out
powercfg /change monitor-timeout-ac 0  | Out-Null
powercfg /change standby-timeout-ac 0  | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null

# 3) disable screensaver
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive   /t REG_SZ /d 0 /f | Out-Null
reg add "HKCU\Control Panel\Desktop" /v ScreenSaverIsSecure /t REG_SZ /d 0 /f | Out-Null

"done"
