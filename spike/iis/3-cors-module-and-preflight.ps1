$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/?linkid=862444' -OutFile C:\setup\IISCORS_amd64.msi
"msi " + (Get-Item C:\setup\IISCORS_amd64.msi).Length + " bytes, signed by: " + (Get-AuthenticodeSignature C:\setup\IISCORS_amd64.msi).SignerCertificate.Subject
$p = Start-Process msiexec.exe -ArgumentList '/i', 'C:\setup\IISCORS_amd64.msi', '/qn', '/norestart', '/l*v', 'C:\setup\iiscors.log' -Wait -PassThru
"msiexec exit $($p.ExitCode)"
# The CORS rule for Groundwork's origin, on Groundwork's application only.
@'
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <cors enabled="true" failUnlistedOrigins="true">
      <add origin="https://app.groundwork.do" allowCredentials="false" maxAge="600">
        <allowHeaders allowAllRequestedHeaders="false">
          <add header="Authorization" />
          <add header="Content-Type" />
        </allowHeaders>
        <allowMethods>
          <add method="GET" />
          <add method="POST" />
        </allowMethods>
        <exposeHeaders>
          <add header="WWW-Authenticate" />
        </exposeHeaders>
      </add>
    </cors>
  </system.webServer>
</configuration>
'@ | Set-Content -Encoding UTF8 C:\Groundwork\IIS\QgisServer\web.config
iisreset /restart | Out-Null
function Pre($path, $origin) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Method Options -Uri "http://localhost$path`?SERVICE=WFS" -Headers @{ Origin = $origin; 'Access-Control-Request-Method' = 'POST'; 'Access-Control-Request-Headers' = 'authorization,content-type' } -TimeoutSec 60
        "$path from $origin -> $([int]$r.StatusCode) allow-origin=[$($r.Headers['Access-Control-Allow-Origin'])] allow-headers=[$($r.Headers['Access-Control-Allow-Headers'])] allow-methods=[$($r.Headers['Access-Control-Allow-Methods'])]" }
  catch { $x = $_.Exception.Response; "$path from $origin -> $([int]$x.StatusCode) allow-origin=[$($x.Headers['Access-Control-Allow-Origin'])]" }
}
Pre '/groundwork/qgisserver' 'https://app.groundwork.do'
Pre '/groundwork/qgisserver' 'https://evil.example'
Pre '/existing/qgisserver' 'https://app.groundwork.do'
# A real request from the allowed origin, without a token: the 401 should still carry the CORS header so the browser can read it.
try { Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost/groundwork/qgisserver?SERVICE=CASTLEGATE&REQUEST=Status&MAP=C:\Groundwork\data\castlegate.qgs' -Headers @{ Origin = 'https://app.groundwork.do' } | Out-Null }
catch { $x = $_.Exception.Response; "status, no token, from allowed origin -> $([int]$x.StatusCode) allow-origin=[$($x.Headers['Access-Control-Allow-Origin'])] expose=[$($x.Headers['Access-Control-Expose-Headers'])] www=[$($x.Headers['WWW-Authenticate'])]" }
"--- QGIS log: any OPTIONS reaching QGIS Server?"
Get-Content C:\Groundwork\IIS\QgisServer\qgis_server.log -Tail 400 | Select-String 'OPTIONS' | Select-Object -Last 3 | ForEach-Object { $_.Line }
