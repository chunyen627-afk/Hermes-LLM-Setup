# Run on the RTX 3070 laptop in an ADMINISTRATOR PowerShell window.
# Enables SSH so the office PC (10.35.219.64, ZeroTier) can set up mining remotely.
# Only the office PC may connect; only the key below is accepted (no password login).
$ErrorActionPreference = 'Stop'
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Start-Service sshd
Set-Service sshd -StartupType Automatic
# Default rule allows everyone; limit it to the office PC.
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Set-NetFirewallRule -RemoteAddress 10.35.219.64
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (office only)' -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 10.35.219.64 -Action Allow | Out-Null
}
# Administrator accounts read keys from this file.
$keys = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $keys -Value 'ssh-ed25519 <YOUR-SSH-PUBLIC-KEY> -Encoding ascii
icacls $keys /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
# Keys only, no passwords.
$config = 'C:\ProgramData\ssh\sshd_config'
(Get-Content $config) -replace '^#?PasswordAuthentication .*', 'PasswordAuthentication no' | Set-Content $config -Encoding ascii
Restart-Service sshd
Write-Host ''
Write-Host "Done. Tell Claude this user name: $env:USERNAME"
