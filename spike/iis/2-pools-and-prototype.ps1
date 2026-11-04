$ErrorActionPreference = 'Stop'
Import-Module WebAdministration
$exe = 'C:\OSGeo4W\apps\qgis-ltr\bin\qgis_mapserv.fcgi.exe'
$gw = 'C:\Groundwork\IIS\QgisServer'
$ex = 'C:\Existing\QgisServer'
$data = 'C:\Groundwork\data'
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"
foreach ($d in "$gw\plugins", $ex, $data, 'C:\setup\pkg') { New-Item -ItemType Directory -Force $d | Out-Null }
Expand-Archive -Path C:\setup\pkg.zip -DestinationPath C:\setup\pkg -Force
Copy-Item C:\setup\pkg\castlegate_spike "$gw\plugins\" -Recurse -Force
Copy-Item C:\setup\pkg\rules.json "$gw\rules.json" -Force

# OSGeo4W's own environment, as its batch file sets it; keep what it adds or changes.
$before = @{}; Get-ChildItem Env: | ForEach-Object { $before[$_.Name] = $_.Value }
$o4w = @{}
cmd /c 'call C:\OSGeo4W\bin\o4w_env.bat >nul 2>&1 && set' | ForEach-Object { $i = $_.IndexOf('='); if ($i -gt 0) { $o4w[$_.Substring(0,$i)] = $_.Substring($i+1) } }
$common = [ordered]@{}
foreach ($k in $o4w.Keys) { if ($before[$k] -ne $o4w[$k] -and $k -notin 'PROMPT','=C:','=ExitCode') { $common[$k] = $o4w[$k] } }
$common['PATH'] = 'C:\OSGeo4W\apps\qgis-ltr\bin;' + $o4w['PATH']
$common['QGIS_PREFIX_PATH'] = 'C:\OSGeo4W\apps\qgis-ltr'
$common['QT_PLUGIN_PATH'] = 'C:\OSGeo4W\apps\qgis-ltr\qtplugins;C:\OSGeo4W\apps\qt5\plugins'
$common['PYTHONPATH'] = 'C:\OSGeo4W\apps\qgis-ltr\python;' + $o4w['PYTHONPATH']
"PYTHONHOME=$($o4w['PYTHONHOME'])  PYTHONEXECUTABLE=$($o4w['PYTHONEXECUTABLE'])  PYTHONPATH=$($common['PYTHONPATH'])"
$common['TEMP'] = 'C:\Windows\TEMP'
$common['QGIS_SERVER_LOG_LEVEL'] = '0'
"environment from o4w_env.bat: " + (($common.Keys | Sort-Object) -join ', ')

# Build the test data and projects with OSGeo4W's Python.
# A batch file, so each variable is read after o4w_env.bat has set it.
@(
  '@echo off',
  'call C:\OSGeo4W\bin\o4w_env.bat',
  'set PATH=C:\OSGeo4W\apps\qgis-ltr\bin;%PATH%',
  'set PYTHONPATH=C:\OSGeo4W\apps\qgis-ltr\python;%PYTHONPATH%',
  'set QGIS_PREFIX_PATH=C:\OSGeo4W\apps\qgis-ltr',
  'set QT_QPA_PLATFORM=offscreen',
  "set CASTLEGATE_OUT=$data",
  '"%PYTHONHOME%\python.exe" C:\setup\pkg\build_project.py'
) | Set-Content -Encoding ASCII C:\setup\build.bat
$ErrorActionPreference = "Continue"; cmd /c C:\setup\build.bat 2>&1 | Select-Object -Last 8; $ErrorActionPreference = "Stop"
Get-ChildItem $data | Select-Object Name, Length | Out-String

# Pools, one per server.
foreach ($pool in 'ExistingQgisServer', 'GroundworkQgisServer') {
  if (-not (Test-Path "IIS:\AppPools\$pool")) { New-WebAppPool $pool | Out-Null }
  Set-ItemProperty "IIS:\AppPools\$pool" managedRuntimeVersion ''
}

# One program, two FastCGI entries, told apart by their arguments.
function Add-Fcgi([string]$arguments, $vars) {
  $filter = "system.webServer/fastCgi/application[@fullPath='$exe' and @arguments='$arguments']"
  if (Get-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter $filter) { Clear-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter $filter }
  Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/fastCgi' -Value @{ fullPath = $exe; arguments = $arguments; maxInstances = 2; activityTimeout = 600; requestTimeout = 600; idleTimeout = 1800; instanceMaxRequests = 10000 }
  foreach ($k in $vars.Keys) { Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter "$filter/environmentVariables" -Value @{ name = $k; value = [string]$vars[$k] } }
}
$exVars = [ordered]@{}; foreach ($k in $common.Keys) { $exVars[$k] = $common[$k] }
$exVars['QGIS_SERVER_LOG_FILE'] = "$ex\qgis_server.log"
$gwVars = [ordered]@{}; foreach ($k in $common.Keys) { $gwVars[$k] = $common[$k] }
$gwVars['QGIS_SERVER_LOG_FILE'] = "$gw\qgis_server.log"
$gwVars['QGIS_PLUGINPATH'] = "$gw\plugins"
$gwVars['CASTLEGATE_ISSUER'] = 'https://login.microsoftonline.com/<tenant id>/v2.0'
$gwVars['CASTLEGATE_AUDIENCE'] = '<app registration id>'
$gwVars['CASTLEGATE_SCOPE'] = 'access_as_user'
$gwVars['CASTLEGATE_TENANT'] = '<tenant id>'
$gwVars['CASTLEGATE_RULES'] = "$gw\rules.json"
Add-Fcgi '' $exVars
Add-Fcgi 'gw' $gwVars

# Two applications on the Default Web Site, each with its pool and handler mapping.
foreach ($a in @(@{ path = '/existing/qgisserver'; dir = $ex; pool = 'ExistingQgisServer'; proc = $exe },
                 @{ path = '/groundwork/qgisserver'; dir = $gw; pool = 'GroundworkQgisServer'; proc = "$exe|gw" })) {
  & $appcmd delete app "Default Web Site$($a.path)" 2>$null | Out-Null
  & $appcmd add app /site.name:"Default Web Site" /path:$($a.path) /physicalPath:$($a.dir) /applicationPool:$($a.pool)
  $loc = "Default Web Site$($a.path)"
  Clear-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Location $loc -Filter "system.webServer/handlers/add[@name='QgisServer']" -ErrorAction SilentlyContinue
  Add-WebConfiguration -PSPath 'MACHINE/WEBROOT/APPHOST' -Location $loc -Filter 'system.webServer/handlers' -AtIndex 0 -Value @{ name = 'QgisServer'; path = '*'; verb = '*'; modules = 'FastCgiModule'; scriptProcessor = $a.proc; resourceType = 'Unspecified'; requireAccess = 'Script' }
}

# Read for both pools; Groundwork's pool may write the data, as it saves.
icacls $gw /grant 'IIS AppPool\GroundworkQgisServer:(OI)(CI)M' | Out-Null
icacls $ex /grant 'IIS AppPool\ExistingQgisServer:(OI)(CI)M' | Out-Null
icacls $data /grant 'IIS AppPool\GroundworkQgisServer:(OI)(CI)M' 'IIS AppPool\ExistingQgisServer:(OI)(CI)RX' | Out-Null
iisreset /restart | Out-Null
& $appcmd list app
& $appcmd list config -section:system.webServer/fastCgi | Select-String 'application fullPath'
foreach ($p in '/existing/qgisserver', '/groundwork/qgisserver') { & $appcmd list config "Default Web Site$p" -section:system.webServer/handlers | Select-String 'QgisServer' }
"done"
