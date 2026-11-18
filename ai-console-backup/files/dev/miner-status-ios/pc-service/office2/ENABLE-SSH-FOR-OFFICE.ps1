# Run on "office 2" (RTX 3070 desktop) in an ADMINISTRATOR PowerShell window:
#   powershell -ExecutionPolicy Bypass -File ENABLE-SSH-FOR-OFFICE.ps1
# Enables SSH so the office PC (10.35.219.64, ZeroTier) can set up mining remotely.
# Only the office PC may connect; only the key below is accepted (no password login).
$ErrorActionPreference = 'Stop'
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Start-Service sshd
Set-Service sshd -StartupType Automatic
# ZeroTier is often a Public network, so the rule must cover every profile; limit it to the office PC.
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (office only)' -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 10.35.219.64 -Profile Any -Action Allow | Out-Null
# Let the office PC ping this one (to check it is up).
Get-NetFirewallRule -Name 'Office-Ping-In' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'Office-Ping-In' -DisplayName 'Ping from office PC' -Direction Inbound -Protocol ICMPv4 -IcmpType 8 -RemoteAddress 10.35.219.64 -Profile Any -Action Allow | Out-Null
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
$gpu = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
if ($gpu) { Write-Host "GPU: $gpu" } else { Write-Host 'GPU: nvidia-smi not found (install the NVIDIA driver first)' }
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '10.35.219.*' } | ForEach-Object { Write-Host "ZeroTier IP: $($_.IPAddress)" }
