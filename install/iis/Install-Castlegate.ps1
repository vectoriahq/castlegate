<#
.SYNOPSIS
Sets up Groundwork's QGIS Server with Castlegate Server under IIS, beside any QGIS Server already there.

.DESCRIPTION
Creates, or brings up to date on a second run:
- the folder for Groundwork's QGIS Server, with the plugin in a plugins folder of its own;
- the GroundworkQgisServer application pool;
- a FastCGI entry for OSGeo4W's qgis_mapserv.fcgi.exe with the argument "gw", carrying
  OSGeo4W's environment, QGIS_PLUGINPATH, the log file and the plugin's settings;
- the IIS application and its handler mapping;
- the CORS rule for Groundwork's origin in that application's web.config, installing
  Microsoft's IIS CORS module first if it is missing.

It changes nothing of an existing QGIS Server: no other pool, FastCGI entry, application or
handler mapping, and not OSGeo4W's own plugin folder, which every QGIS Server on the machine
searches. It stops Groundwork's pool while it changes what the pool runs from, starts it once every change has succeeded, and touches no other pool. It ends with the status check.

Run it in Windows PowerShell opened with "Run as administrator". -Uninstall removes what it
created and leaves the folder.

.EXAMPLE
.\Install-Castlegate.ps1 -TenantId 11111111-... -ClientId 22222222-... -PluginZip .\castlegate_server-0.1.0.zip -CheckMap C:\Projects\assets.qgs

.EXAMPLE
.\Install-Castlegate.ps1 -TenantId 11111111-... -ClientId 22222222-... -PluginZip .\castlegate_server-0.1.0.zip -Credential (Get-Credential DOMAIN\svc-groundwork) -DataFolder D:\GIS\Projects
#>
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Install')]
param(
    # The Entra tenant (directory) ID. Required unless -Issuer names another provider.
    [Parameter(ParameterSetName = 'Install')][string]$TenantId,
    # The application (client) ID of the app registration for this QGIS Server.
    [Parameter(Mandatory, ParameterSetName = 'Install')][string]$ClientId,
    # The release zip holding castlegate_server/.
    [Parameter(Mandatory, ParameterSetName = 'Install')][string]$PluginZip,
    # The account the pool runs as, which needs write rights on the data Groundwork saves to.
    # Without it the pool runs as its own identity, IIS AppPool\GroundworkQgisServer.
    [Parameter(ParameterSetName = 'Install')][pscredential]$Credential,
    # Folders the pool may write, such as the projects' GeoPackages. Database layers need none.
    [Parameter(ParameterSetName = 'Install')][string[]]$DataFolder = @(),
    # A project the server opens, for the status check at the end.
    [Parameter(ParameterSetName = 'Install')][string]$CheckMap,
    [Parameter(ParameterSetName = 'Install')][string]$Scope = 'access_as_user',
    # The OpenID Connect issuer, for a provider other than Entra. Entra's is built from -TenantId.
    [Parameter(ParameterSetName = 'Install')][string]$Issuer,
    [Parameter(ParameterSetName = 'Install')][string]$Origin = 'https://app.groundwork.do',
    # Further environment variables for Groundwork's QGIS Server, such as SSL_CERT_FILE.
    [Parameter(ParameterSetName = 'Install')][hashtable]$Environment = @{},
    [Parameter(Mandatory, ParameterSetName = 'Uninstall')][switch]$Uninstall,
    [string]$OSGeo4WRoot = 'C:\OSGeo4W',
    # The QGIS Server package under apps\: qgis-ltr for the long-term release.
    [string]$QgisApp = 'qgis-ltr',
    [string]$Root = 'C:\Groundwork\IIS\QgisServer',
    [string]$Site = 'Default Web Site',
    [string]$AppPath = '/groundwork/qgisserver',
    [string]$Pool = 'GroundworkQgisServer'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

$FastCgiArguments = 'gw'
$HandlerName = 'CastlegateQgisServer'
$Exe = Join-Path $OSGeo4WRoot "apps\$QgisApp\bin\qgis_mapserv.fcgi.exe"
$Plugins = Join-Path $Root 'plugins'
$Logs = Join-Path $Root 'logs'
$AppHost = 'MACHINE/WEBROOT/APPHOST'
$Location = "$Site$AppPath"
$FastCgiFilter = "system.webServer/fastCgi/application[@fullPath='$Exe' and @arguments='$FastCgiArguments']"

function Say([string]$message) { Write-Host "==> $message" }

function Stop-Install([string]$message) {
    Write-Host "STOPPED: $message" -ForegroundColor Red
    exit 1
}

# ---- checks before changing anything -----------------------------------------------------

if (-not $Uninstall -and -not $TenantId -and -not $Issuer) {
    Stop-Install 'pass -TenantId for Entra, or -Issuer for another OpenID Connect provider.'
}

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Stop-Install 'run this in PowerShell opened with "Run as administrator".'
}
Import-Module WebAdministration

# ---- uninstall ---------------------------------------------------------------------------

if ($Uninstall) {
    if ($PSCmdlet.ShouldProcess($Location, 'remove the application, its handler mapping, the FastCGI entry and the pool')) {
        if (Get-WebApplication -Site $Site -Name $AppPath.TrimStart('/')) { Remove-WebApplication -Site $Site -Name $AppPath.TrimStart('/'); Say "removed the application $Location" }
        Clear-WebConfiguration -PSPath $AppHost -Location $Location -Filter "system.webServer/handlers/add[@name='$HandlerName']" -ErrorAction SilentlyContinue
        if (Get-WebConfiguration -PSPath $AppHost -Filter $FastCgiFilter) { Clear-WebConfiguration -PSPath $AppHost -Filter $FastCgiFilter; Say "removed the FastCGI entry $Exe|$FastCgiArguments" }
        if (Test-Path "IIS:\AppPools\$Pool") { Remove-WebAppPool $Pool; Say "removed the pool $Pool" }
        Say "left $Root in place; delete it yourself when you no longer need its log or web.config"
    }
    exit 0
}

# ---- the folder rules --------------------------------------------------------------------
#
# No one but administrators and the system may change $Root or anything in it, except that the
# pool's account may write in $Logs; no one else may rename, delete or re-permission any folder
# above it; and $Plugins holds Castlegate Server alone.
# Everything that changes these folders goes through Protect-Ancestors and Set-FolderRule.

$Admins = '*S-1-5-32-544'
$System = '*S-1-5-18'
$Users = '*S-1-5-32-545'
# Administrators, the system, TrustedInstaller, and whoever is running this as administrator.
$Trusted = 'S-1-5-32-544', 'S-1-5-18', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464',
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Rights on a folder that let someone replace what is in it or below it.
$Replacing = [Security.AccessControl.FileSystemRights]'Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'

function Get-Ancestors {
    # The folders above $Root, nearest first, the drive's root last.
    $found = @()
    $parent = Split-Path ([IO.Path]::GetFullPath($Root)) -Parent
    while ($parent) { $found += $parent; $parent = Split-Path $parent -Parent }
    $found
}

function Protect-Ancestors {
    $ancestors = @(Get-Ancestors)
    # Folders this creates above $Root are protected like $Root: read for everyone, changed by administrators.
    $creatable = @($ancestors | Select-Object -SkipLast 1 | Sort-Object Length)
    foreach ($folder in $creatable) {
        if (-not (Test-Path $folder)) {
            New-Item -ItemType Directory $folder | Out-Null
            icacls $folder /setowner $Admins /C /Q | Out-Null
            icacls $folder /inheritance:r /grant:r "${Admins}:(OI)(CI)F" "${System}:(OI)(CI)F" "${Users}:(OI)(CI)RX" | Out-Null
            Say "created $folder for administrators alone to change"
        }
    }
    Assert-Ancestors
}

function Assert-Ancestors {
    # Each folder above that exists must leave $Root where it is: a trusted owner, and no one
    # else able to delete it, delete what is in it, or change its rights.
    foreach ($folder in @(Get-Ancestors | Where-Object { Test-Path $_ })) {
        $acl = Get-Acl $folder
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -notin $Trusted) {
            Stop-Install "$folder is owned by $(([Security.Principal.SecurityIdentifier]$owner).Translate([Security.Principal.NTAccount])), who could replace $Root. Give it to administrators, or pass another -Root."
        }
        $risky = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) | Where-Object {
            $_.AccessControlType -eq 'Allow' -and
            -not ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
            ($_.FileSystemRights -band $Replacing) -and
            $_.IdentityReference.Value -notin $Trusted
        })
        if ($risky.Count) {
            $who = ($risky | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.NTAccount]).Value } | Sort-Object -Unique) -join ', '
            Stop-Install "$folder lets $who delete or re-permission what is in it, so they could replace $Root. Remove those rights, or pass another -Root."
        }
    }
}

function Set-FolderRule {
    # An owner can always rewrite a folder's rights, so administrators own everything first.
    New-Item -ItemType Directory -Force $Root, $Logs | Out-Null
    icacls $Root /setowner $Admins /T /C /Q | Out-Null
    # Everything inside then takes its rights from $Root alone, and $Root gets exactly these:
    # administrators and the system in full, the pool's account read, and write in logs\ alone.
    icacls $Root /reset /T /C /Q | Out-Null
    icacls $Root /inheritance:r /grant:r "${Admins}:(OI)(CI)F" "${System}:(OI)(CI)F" "${identity}:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install "could not set the rights on $Root." }
    icacls $Logs /grant:r "${identity}:(OI)(CI)M" | Out-Null
}

# ---- checks an install needs, before anything changes -----------------------------------
#
# Everything that can fail on bad input or a missing part happens here, so a mistake on a
# rerun stops with the working install untouched: the folders above $Root, the zip, OSGeo4W's
# environment and the CORS module.

if (-not (Get-WebGlobalModule -Name FastCgiModule -ErrorAction SilentlyContinue)) {
    Stop-Install "IIS has no FastCGI module. Add the CGI role service to IIS (Server Manager, Web Server, Application Development, CGI) and run this again."
}
if (-not (Test-Path $Exe)) {
    Stop-Install "QGIS Server is not at $Exe. Install OSGeo4W's $QgisApp-server package, or pass -OSGeo4WRoot and -QgisApp."
}
$fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
$fullO4w = [IO.Path]::GetFullPath($OSGeo4WRoot).TrimEnd('\') + '\'
if ($fullRoot.StartsWith($fullO4w, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Install "-Root is inside $OSGeo4WRoot. The plugin must never go under OSGeo4W, whose plugin folder every QGIS Server on this machine searches."
}

# The folders above $Root that exist already.
Assert-Ancestors

# The release zip, unpacked and checked in a temporary folder.
if (-not (Test-Path $PluginZip -PathType Leaf)) { Stop-Install "there is no file at $PluginZip; pass the release zip." }
$unpack = Join-Path ([IO.Path]::GetTempPath()) ("castlegate-" + [guid]::NewGuid())
try { Expand-Archive -Path $PluginZip -DestinationPath $unpack } catch { Stop-Install "$PluginZip could not be unpacked: $($_.Exception.Message)" }
$unpacked = Join-Path $unpack 'castlegate_server'
$metadata = Join-Path $unpacked 'metadata.txt'
if (-not (Test-Path $metadata) -or -not (Test-Path (Join-Path $unpacked '__init__.py'))) {
    Remove-Item $unpack -Recurse -Force
    Stop-Install "$PluginZip does not hold castlegate_server\ with metadata.txt and __init__.py; pass the release zip."
}
$version = (Select-String -Path $metadata -Pattern '^version=(.+)$').Matches[0].Groups[1].Value

# What o4w_env.bat adds or changes, as OSGeo4W's own Apache configuration has QGIS Server run
# with it. It runs from a clean start: Windows' own folders as PATH and no Python settings, so
# nothing in this PowerShell window, such as another Python on its PATH, reaches the server.
$windowsPath = @("$env:windir\system32", $env:windir, "$env:windir\System32\Wbem") -join ';'
$before = @{}
Get-ChildItem Env: | ForEach-Object { $before[$_.Name] = $_.Value }
$o4w = @{}
cmd /c "set `"PATH=$windowsPath`"&& set PYTHONHOME=&& set PYTHONPATH=&& call `"$OSGeo4WRoot\bin\o4w_env.bat`" >nul 2>&1 && set" | ForEach-Object {
    $i = $_.IndexOf('=')
    if ($i -gt 0) { $o4w[$_.Substring(0, $i)] = $_.Substring($i + 1) }
}
if (-not $o4w['OSGEO4W_ROOT'] -or -not $o4w['PYTHONHOME']) {
    Stop-Install "$OSGeo4WRoot\bin\o4w_env.bat did not set OSGEO4W_ROOT and PYTHONHOME; check the OSGeo4W install."
}
$vars = [ordered]@{}
foreach ($k in ($o4w.Keys | Sort-Object)) {
    if ($before[$k] -ne $o4w[$k] -and $k -notin 'PROMPT', 'PATH') { $vars[$k] = $o4w[$k] }
}
# These two even where this window already has the same values, which IIS's own environment may not.
$vars['OSGEO4W_ROOT'] = $o4w['OSGEO4W_ROOT']
$vars['PYTHONHOME'] = $o4w['PYTHONHOME']
$app = Join-Path $OSGeo4WRoot "apps\$QgisApp"
$vars['PATH'] = "$app\bin;" + $o4w['PATH']
$vars['QGIS_PREFIX_PATH'] = $app
# Qt 5's plugins for QGIS 3, Qt 6's for QGIS 4: whichever OSGeo4W has.
$qt = @("$app\qtplugins") + (@('qt5', 'qt6') | ForEach-Object { Join-Path $OSGeo4WRoot "apps\$_\plugins" } | Where-Object { Test-Path $_ })
$vars['QT_PLUGIN_PATH'] = $qt -join ';'
$vars['PYTHONPATH'] = "$app\python;" + $o4w['PYTHONPATH']
$vars['TEMP'] = Join-Path $env:windir 'TEMP'
$vars['QGIS_PLUGINPATH'] = $Plugins
$vars['QGIS_SERVER_LOG_FILE'] = Join-Path $Logs 'qgis_server.log'
# Warning keeps the plugin's audit lines, which it writes at Warning.
$vars['QGIS_SERVER_LOG_LEVEL'] = '1'
# The plugin folder is read-only to the server, so Python writes no compiled copies there.
$vars['PYTHONDONTWRITEBYTECODE'] = '1'
$vars['CASTLEGATE_ISSUER'] = if ($Issuer) { $Issuer } else { "https://login.microsoftonline.com/$TenantId/v2.0" }
$vars['CASTLEGATE_AUDIENCE'] = $ClientId
$vars['CASTLEGATE_SCOPE'] = $Scope
# Only with a tenant: the plugin then refuses a token whose tid differs, and other providers send no tid.
if ($TenantId) { $vars['CASTLEGATE_TENANT'] = $TenantId }
foreach ($k in $Environment.Keys) { $vars[$k] = [string]$Environment[$k] }

if (-not (Get-WebGlobalModule -Name CorsModule -ErrorAction SilentlyContinue)) {
    if ($PSCmdlet.ShouldProcess('IIS CORS module', 'download from Microsoft and install')) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $msi = Join-Path ([IO.Path]::GetTempPath()) 'IISCORS_amd64.msi'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/?linkid=862444' -OutFile $msi
        $signature = Get-AuthenticodeSignature $msi
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            Stop-Install "the downloaded IIS CORS module is not validly signed by Microsoft ($($signature.Status)); install it yourself from https://www.iis.net/downloads/microsoft/iis-cors-module and run this again."
        }
        $p = Start-Process msiexec.exe -ArgumentList '/i', "`"$msi`"", '/qn', '/norestart' -Wait -PassThru
        if ($p.ExitCode -ne 0) { Stop-Install "the IIS CORS module's installer ended with code $($p.ExitCode)." }
        Remove-Item $msi -Force
        Say 'installed the IIS CORS module'
    }
}

# ---- the pool ----------------------------------------------------------------------------

$identity = if ($Credential) { $Credential.UserName } else { "IIS AppPool\$Pool" }
if ($PSCmdlet.ShouldProcess($Pool, "create or update the application pool, running as $identity")) {
    if (-not (Test-Path "IIS:\AppPools\$Pool")) { New-WebAppPool $Pool | Out-Null; Say "created the pool $Pool" }
    Set-ItemProperty "IIS:\AppPools\$Pool" managedRuntimeVersion ''
    if ($Credential) {
        Set-ItemProperty "IIS:\AppPools\$Pool" processModel -Value @{
            userName = $Credential.UserName; password = $Credential.GetNetworkCredential().Password; identityType = 3
        }
    } else {
        Set-ItemProperty "IIS:\AppPools\$Pool" processModel.identityType -Value 4
    }
    Say "the pool runs as $identity"
    # Stopped while anything it runs from changes, and started only once every change has
    # succeeded: a QGIS Server process started in between could find no plugin or no plugin path
    # and answer everyone. If this stops part-way, the pool stays stopped and IIS refuses
    # Groundwork's requests until a run succeeds.
    if ((Get-WebAppPoolState $Pool).Value -ne 'Stopped') {
        Stop-WebAppPool $Pool
        for ($i = 0; $i -lt 60 -and (Get-WebAppPoolState $Pool).Value -ne 'Stopped'; $i++) { Start-Sleep -Seconds 1 }
        if ((Get-WebAppPoolState $Pool).Value -ne 'Stopped') { Stop-Install "the pool $Pool did not stop, so nothing was changed under it." }
        Say "stopped $Pool while it is set up"
    }
}

# ---- the folder and the plugin -----------------------------------------------------------

if ($PSCmdlet.ShouldProcess($Root, "protect the folder and put Castlegate Server $version in it")) {
    Protect-Ancestors
    Set-FolderRule
    # The new plugins folder is made beside the old one, then the two are swapped by renaming, so
    # the plugins folder is replaced whole and nothing else in it survives to load beside
    # Castlegate. The old one is deleted only once the new one is in place. Copied, not moved:
    # a move within a drive keeps the temporary folder's rights, which only this user has.
    $staging = Join-Path $Root ('plugins-new-' + [guid]::NewGuid())
    New-Item -ItemType Directory $staging | Out-Null
    Copy-Item $unpacked (Join-Path $staging 'castlegate_server') -Recurse
    $old = $null
    if (Test-Path $Plugins) {
        $old = Join-Path $Root ('plugins-old-' + [guid]::NewGuid())
        Rename-Item $Plugins (Split-Path $old -Leaf)
    }
    Rename-Item $staging (Split-Path $Plugins -Leaf)
    if ($old) { Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue }
    # Left-overs of an earlier run that stopped part-way.
    Get-ChildItem $Root -Directory | Where-Object { $_.Name -like 'plugins-*' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Say "Castlegate Server $version is in $(Join-Path $Plugins 'castlegate_server')"
}
Remove-Item $unpack -Recurse -Force -ErrorAction SilentlyContinue

# ---- the FastCGI entry -------------------------------------------------------------------

if ($PSCmdlet.ShouldProcess("$Exe|$FastCgiArguments", 'create or replace the FastCGI entry')) {
    # IIS keys a FastCGI application by its program and arguments together, so this entry is
    # Groundwork's alone; an entry for the same program with other arguments is left as it is.
    # Saved to applicationHost.config as one change, so no reader sees the entry half rebuilt.
    Start-WebCommitDelay
    try {
        if (Get-WebConfiguration -PSPath $AppHost -Filter $FastCgiFilter) { Clear-WebConfiguration -PSPath $AppHost -Filter $FastCgiFilter }
        Add-WebConfiguration -PSPath $AppHost -Filter 'system.webServer/fastCgi' -Value @{
            fullPath = $Exe; arguments = $FastCgiArguments; maxInstances = 4
            activityTimeout = 600; requestTimeout = 600; idleTimeout = 1800; instanceMaxRequests = 10000
        }
        foreach ($k in $vars.Keys) {
            Add-WebConfiguration -PSPath $AppHost -Filter "$FastCgiFilter/environmentVariables" -Value @{ name = $k; value = [string]$vars[$k] }
        }
        Stop-WebCommitDelay -Commit $true
    } catch {
        Stop-WebCommitDelay -Commit $false
        Stop-Install "the FastCGI entry could not be written, and was left as it was: $($_.Exception.Message)"
    }
    Say "the FastCGI entry $Exe|$FastCgiArguments has $($vars.Count) environment variables"
}

# ---- the application and its handler mapping ---------------------------------------------

if ($PSCmdlet.ShouldProcess($Location, 'create the application and its handler mapping')) {
    $name = $AppPath.TrimStart('/')
    $existing = Get-WebApplication -Site $Site -Name $name
    if ($existing) {
        Set-ItemProperty "IIS:\Sites\$Location" -Name applicationPool -Value $Pool
        Set-ItemProperty "IIS:\Sites\$Location" -Name physicalPath -Value $Root
    } else {
        # appcmd, as package 1 ran it, creates /groundwork/qgisserver with no /groundwork above it.
        & "$env:windir\system32\inetsrv\appcmd.exe" add app /site.name:"$Site" /path:$AppPath /physicalPath:$Root /applicationPool:$Pool | Out-Null
        if ($LASTEXITCODE -ne 0) { Stop-Install "appcmd could not create the application $Location." }
        Say "created the application $Location"
    }
    Clear-WebConfiguration -PSPath $AppHost -Location $Location -Filter "system.webServer/handlers/add[@name='$HandlerName']" -ErrorAction SilentlyContinue
    Add-WebConfiguration -PSPath $AppHost -Location $Location -Filter 'system.webServer/handlers' -AtIndex 0 -Value @{
        name = $HandlerName; path = '*'; verb = '*'; modules = 'FastCgiModule'
        scriptProcessor = "$Exe|$FastCgiArguments"; resourceType = 'Unspecified'; requireAccess = 'Script'
    }
    Say "requests to $AppPath go to $Exe|$FastCgiArguments"
    # Anonymous requests run as the pool's account, not IIS's IUSR, which has no rights on the folder.
    Set-WebConfigurationProperty -PSPath $AppHost -Location $Location -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name userName -Value ''
}

# ---- the CORS rule ----------------------------------------------------------------------

if ($PSCmdlet.ShouldProcess((Join-Path $Root 'web.config'), "write the CORS rule for $Origin")) {
    # The browser's preflight is answered here, because QGIS Server does not answer OPTIONS.
    # WWW-Authenticate is exposed so Groundwork can tell "sign in" from "the plugin is missing".
    $escaped = [Security.SecurityElement]::Escape($Origin)
    @"
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <cors enabled="true" failUnlistedOrigins="true">
      <add origin="$escaped" allowCredentials="false" maxAge="600">
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
"@ | Set-Content -Encoding UTF8 (Join-Path $Root 'web.config')
    Say "the CORS rule allows $Origin"
}

# ---- rights, restart, check --------------------------------------------------------------

if ($PSCmdlet.ShouldProcess($Root, "set the folder's rights: $identity may read it and write only its logs folder")) {
    # Again, so web.config and anything else written since follow the rule.
    Set-FolderRule
    foreach ($folder in $DataFolder) { icacls $folder /grant "${identity}:(OI)(CI)M" | Out-Null; Say "$identity may write $folder" }
    Say "only administrators and the system can change $Root; $identity reads it and writes only $Logs"
}

if ($PSCmdlet.ShouldProcess($Pool, 'start the pool')) {
    # Only now, with every change made: see the pool step.
    Start-WebAppPool $Pool
    Say "started $Pool; no other pool was touched"
}

$python = Join-Path $o4w['PYTHONHOME'] 'python.exe'
$check = Join-Path $Plugins 'castlegate_server\tools\check.py'
$address = "http://localhost$AppPath"
# Not in a -WhatIf preview, which has changed nothing to check.
if ($CheckMap -and -not $WhatIfPreference) {
    Say "status check against $address"
    & cmd /c "call `"$OSGeo4WRoot\bin\o4w_env.bat`" >nul 2>&1 && `"$python`" `"$check`" $address --map `"$CheckMap`" --origin $Origin"
    if ($LASTEXITCODE -ne 0) { Stop-Install "the status check failed; the server log is $($vars['QGIS_SERVER_LOG_FILE'])." }
} else {
    Say "done. Check it with a project the server opens:"
    Write-Host "  `"$python`" `"$check`" $address --map <project path> --origin $Origin"
}
