$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force C:\setup | Out-Null
$log = 'C:\setup\install.log'
"start $(Get-Date -Format o)" | Out-File $log
Install-WindowsFeature Web-Server, Web-CGI, Web-Mgmt-Tools, Web-Http-Logging, Web-Request-Monitor | Out-String | Out-File $log -Append
"iis done $(Get-Date -Format o)" | Out-File $log -Append
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -UseBasicParsing -Uri https://download.osgeo.org/osgeo4w/v2/osgeo4w-setup.exe -OutFile C:\setup\osgeo4w-setup.exe
"setup downloaded $(Get-Date -Format o)" | Out-File $log -Append
$p = Start-Process -FilePath C:\setup\osgeo4w-setup.exe -ArgumentList '--advanced','--quiet-mode','--autoaccept','--root','C:\OSGeo4W','--site','https://download.osgeo.org/osgeo4w/v2','--local-package-dir','C:\setup\osgeo4w-packages','--packages','qgis-ltr-server' -Wait -PassThru -RedirectStandardOutput C:\setup\osgeo4w-out.log -RedirectStandardError C:\setup\osgeo4w-err.log
"osgeo4w exit $($p.ExitCode) $(Get-Date -Format o)" | Out-File $log -Append
Get-ChildItem C:\OSGeo4W\apps\qgis-ltr\bin\qgis_mapserv.fcgi.exe -ErrorAction SilentlyContinue | Select-Object FullName, Length | Out-String | Out-File $log -Append
"finished $(Get-Date -Format o)" | Out-File $log -Append
