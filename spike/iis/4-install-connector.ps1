$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$f = 'C:\setup\MicrosoftEntraPrivateNetworkConnectorInstaller.exe'
Invoke-WebRequest -UseBasicParsing -Uri 'https://download.msappproxy.net/Subscription/d3c8b69d-6bf7-42be-a529-3fe9c2e70c90/Connector/DownloadConnectorInstaller' -OutFile $f
$sig = Get-AuthenticodeSignature $f
"installer $((Get-Item $f).Length) bytes, signature $($sig.Status), signer $($sig.SignerCertificate.Subject), version $((Get-Item $f).VersionInfo.ProductVersion)"
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { 'not a valid Microsoft-signed file; stopping'; exit 1 }
# The connector's documented prerequisite on Windows Server 2019 and later, for application proxy: HTTP/2 off in WinHttp.
$key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp'
if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
Set-ItemProperty -Path $key -Name 'EnableDefaultHTTP2' -Value 0 -Type DWord
$p = Start-Process $f -ArgumentList 'REGISTERCONNECTOR="false"', '/q' -Wait -PassThru
"installer exit $($p.ExitCode)"
Get-Service | Where-Object { $_.DisplayName -match 'private network connector' } | Select-Object DisplayName, Status | Out-String
Test-Path 'C:\Program Files\Microsoft Entra private network connector\RegisterConnector.ps1'
