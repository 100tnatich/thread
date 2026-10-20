# À lancer UNE FOIS dans PowerShell **administrateur** sur le PC Windows.
# Ouvre les ports 80 et 443 vers WSL (réseau WSL en mode « mirrored »).
# Retrait: voir la fin du fichier.

$ErrorActionPreference = 'Stop'

# 1. Pare-feu Windows: HTTP (80, requis par Let's Encrypt) et HTTPS (443, TCP + UDP pour HTTP/3).
New-NetFirewallRule -DisplayName 'Thread HTTP/HTTPS (TCP)' -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 80,443 -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'Thread HTTP/3 (UDP)' -Direction Inbound -Action Allow `
  -Protocol UDP -LocalPort 443 -Profile Any | Out-Null

# 2. Pare-feu Hyper-V (celui qui filtre WSL en mode mirrored). Une règle limitée à ces ports,
#    pas un « tout autoriser ». Identifiant WSL = {40E0AC32-46A5-438A-A0B2-2B479E8F2E90}.
New-NetFirewallHyperVRule -Name 'Thread-WSL-Web' -DisplayName 'Thread WSL web' -Direction Inbound `
  -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 80,443 | Out-Null

Write-Host 'OK: ports 80/443 ouverts sur Windows et vers WSL.'

# Retrait (quand le vrai serveur est en place):
#   Remove-NetFirewallRule -DisplayName 'Thread HTTP/HTTPS (TCP)','Thread HTTP/3 (UDP)'
#   Remove-NetFirewallHyperVRule -Name 'Thread-WSL-Web'
