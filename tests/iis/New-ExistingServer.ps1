<#
.SYNOPSIS
For the IIS tests: a QGIS Server set up the way target clients already run one, with no plugin.

A pool of its own, a FastCGI entry for OSGeo4W's qgis_mapserv.fcgi.exe with no arguments and
OSGeo4W's environment, and the application /existing/qgisserver. Install-Castlegate.ps1 must
leave all of it as it is.
#>
param(
    [string]$OSGeo4WRoot = 'C:\OSGeo4W',
    [string]$QgisApp = 'qgis-ltr',
    [string]$Folder = 'C:\Existing\QgisServer',
    [string]$DataFolder = 'C:\castlegate\data'
)
$ErrorActionPreference = 'Stop'
Import-Module WebAdministration
$exe = Join-Path $OSGeo4WRoot "apps\$QgisApp\bin\qgis_mapserv.fcgi.exe"
$app = Join-Path $OSGeo4WRoot "apps\$QgisApp"
New-Item -ItemType Directory -Force $Folder | Out-Null

$before = @{}; Get-ChildItem Env: | ForEach-Object { $before[$_.Name] = $_.Value }
$o4w = @{}
cmd /c "call `"$OSGeo4WRoot\bin\o4w_env.bat`" >nul 2>&1 && set" | ForEach-Object { $i = $_.IndexOf('='); if ($i -gt 0) { $o4w[$_.Substring(0, $i)] = $_.Substring($i + 1) } }
$vars = [ordered]@{}
foreach ($k in ($o4w.Keys | Sort-Object)) { if ($before[$k] -ne $o4w[$k] -and $k -ne 'PROMPT') { $vars[$k] = $o4w[$k] } }
$vars['PATH'] = "$app\bin;" + $o4w['PATH']
$vars['QGIS_PREFIX_PATH'] = $app
$vars['PYTHONPATH'] = "$app\python;" + $o4w['PYTHONPATH']
$vars['TEMP'] = Join-Path $env:windir 'TEMP'
$vars['QGIS_SERVER_LOG_FILE'] = Join-Path $Folder 'qgis_server.log'
$vars['QGIS_SERVER_LOG_LEVEL'] = '0'

New-WebAppPool ExistingQgisServer | Out-Null
Set-ItemProperty IIS:\AppPools\ExistingQgisServer managedRuntimeVersion ''
Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/fastCgi' -Value @{ fullPath = $exe; arguments = ''; maxInstances = 2 }
$filter = "system.webServer/fastCgi/application[@fullPath='$exe' and @arguments='']"
foreach ($k in $vars.Keys) { Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter "$filter/environmentVariables" -Value @{ name = $k; value = [string]$vars[$k] } }
& "$env:windir\system32\inetsrv\appcmd.exe" add app /site.name:"Default Web Site" /path:/existing/qgisserver /physicalPath:$Folder /applicationPool:ExistingQgisServer | Out-Null
Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Location 'Default Web Site/existing/qgisserver' -Filter 'system.webServer/handlers' -AtIndex 0 -Value @{
    name = 'QgisServer'; path = '*'; verb = '*'; modules = 'FastCgiModule'; scriptProcessor = $exe; resourceType = 'Unspecified'; requireAccess = 'Script'
}
icacls $Folder /grant 'IIS AppPool\ExistingQgisServer:(OI)(CI)M' | Out-Null
icacls $DataFolder /grant 'IIS AppPool\ExistingQgisServer:(OI)(CI)RX' | Out-Null
"the existing QGIS Server answers at /existing/qgisserver"
