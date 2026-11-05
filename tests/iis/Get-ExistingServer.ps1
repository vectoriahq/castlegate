<#
.SYNOPSIS
For the IIS tests: the existing QGIS Server's configuration as text, to compare before and after.
#>
param([string]$OSGeo4WRoot = 'C:\OSGeo4W', [string]$QgisApp = 'qgis-ltr')
$ErrorActionPreference = 'Stop'
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"
$exe = Join-Path $OSGeo4WRoot "apps\$QgisApp\bin\qgis_mapserv.fcgi.exe"
& $appcmd list apppool ExistingQgisServer /config:*
& $appcmd list app 'Default Web Site/existing/qgisserver' /config:*
& $appcmd list config 'Default Web Site/existing/qgisserver' -section:system.webServer/handlers
(Get-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter "system.webServer/fastCgi/application[@fullPath='$exe' and @arguments='']/environmentVariables/environmentVariable" |
    ForEach-Object { "$($_.name)=$($_.value)" }) -join "`n"
