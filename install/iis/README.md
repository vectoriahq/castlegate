# Castlegate Server under IIS

`Install-Castlegate.ps1` sets up a QGIS Server for Groundwork, with Castlegate Server, beside a QGIS Server the machine may already run. It is the profile for the setup many organisations already have: OSGeo4W's QGIS Server run by IIS through FastCGI, reached from outside through Entra application proxy. A GitHub Windows Server machine runs it end to end on every change to it (`.github/workflows/iis.yml`), with test sign-ins rather than a real Entra tenant.

## What it creates

```text
Default Web Site
├── /existing/qgisserver     → qgis_mapserv.fcgi.exe       its own pool      (left as it is)
└── /groundwork/qgisserver   → qgis_mapserv.fcgi.exe|gw    pool GroundworkQgisServer
                               QGIS_PLUGINPATH=C:\Groundwork\IIS\QgisServer\plugins
                               QGIS_SERVER_LOG_FILE=C:\Groundwork\IIS\QgisServer\qgis_server.log
                               CASTLEGATE_ISSUER, _AUDIENCE, _SCOPE, _TENANT
```

- **A folder,** `C:\Groundwork\IIS\QgisServer`, with the plugin in `plugins\castlegate_server`, the log, and a `web.config` holding the CORS rule.
- **A pool,** `GroundworkQgisServer`, running as its own identity or as the account you give.
- **A FastCGI entry** for OSGeo4W's `qgis_mapserv.fcgi.exe` with the argument `gw`. IIS tells FastCGI entries apart by program and arguments together, so it is a second entry for the same program, with its own environment variables: OSGeo4W's, the plugin folder, the log, and the plugin's settings.
- **The application** `/groundwork/qgisserver`, with a handler mapping to that entry.
- **The CORS rule** for `https://app.groundwork.do`: the browser's preflight answered, `WWW-Authenticate` exposed, other origins refused. It installs Microsoft's IIS CORS module first if the machine lacks it, after checking the download is signed by Microsoft.

It changes nothing of an existing QGIS Server: no other pool, FastCGI entry, application or handler mapping. It never puts the plugin in OSGeo4W's own plugin folder, which every QGIS Server on the machine searches, and it stops if `-Root` is under OSGeo4W. It recycles only Groundwork's pool. Running it again brings everything up to date.

## Before you run it

- **IIS with the CGI role service** (Server Manager, Web Server, Application Development, CGI).
- **OSGeo4W's `qgis-ltr-server` package**, at `C:\OSGeo4W` unless you say otherwise with `-OSGeo4WRoot`.
- **The release zip,** `castlegate_server-<version>.zip`.
- **An app registration in Entra for this QGIS Server:**
  1. Under **Expose an API**, add a scope, such as `access_as_user`.
  2. Under **Expose an API**, **Authorized client applications**, add Groundwork's application ID, which Vectoria gives you, with that scope. Your staff then see no consent prompt.
  3. In the **Manifest**, set `requestedAccessTokenVersion` to `2`, so tokens have the issuer `https://login.microsoftonline.com/<tenant ID>/v2.0`.
  4. If rules name groups, under **Token configuration**, add the groups claim. A person in more than 200 groups is refused; [`docs/limits.md`](../../docs/limits.md) says why.
- **The account the pool runs as.** Groundwork saves, so it needs write rights on the data, where an existing QGIS Server's account is often read-only. Without `-Credential` the pool runs as `IIS AppPool\GroundworkQgisServer`; layers in a GeoPackage need `-DataFolder` for that identity to write them, and layers in a database need the database login in the project's connection.

## Running it

In Windows PowerShell opened with **Run as administrator**:

```powershell
.\Install-Castlegate.ps1 -TenantId <tenant ID> -ClientId <the app registration's application (client) ID> -PluginZip .\castlegate_server-0.1.0.zip -CheckMap C:\Projects\assets.qgs
```

With a domain service account and the folder of GeoPackages it saves to:

```powershell
.\Install-Castlegate.ps1 -TenantId <tenant ID> -ClientId <client ID> -PluginZip .\castlegate_server-0.1.0.zip -Credential (Get-Credential DOMAIN\svc-groundwork) -DataFolder D:\GIS\Projects -CheckMap D:\GIS\Projects\assets.qgs
```

`-WhatIf` lists what it would change without changing it. It ends with the status check from [`install/common/`](../common/README.md) when `-CheckMap` names a project the server opens. Each project names its rules layer in the `castlegate_rules_layer` project variable ([`docs/rules-table.md`](../../docs/rules-table.md)).

To remove it, which leaves the folder and its log:

```powershell
.\Install-Castlegate.ps1 -Uninstall
```

## Reaching it from outside

If the existing QGIS Server is reached from outside through Entra application proxy with Entra pre-authentication, publish Groundwork's path as a **second** application proxy app, with **pre-authentication set to Passthrough** and `/groundwork/qgisserver` as its internal and external path. Application proxy with Entra pre-authentication does not answer a browser on Groundwork's origin; with passthrough, the plugin is the lock. The connector the machine already has carries it.

Setting up a new connector, as checked on Windows Server 2022: switch HTTP/2 off for WinHttp and Internet Explorer's Enhanced Security off for administrators, then register the connector in the installer's own sign-in window. A silent install registered with a device-code token was refused. Application proxy needs Entra ID P1 or P2.

## Layers in SQL Server

QGIS's SQL Server provider uses the ODBC driver a layer's connection names in its `service` setting; without one it uses `driver={SQL Server}`, the older driver that ships with Windows. The plugin's tests ran with Microsoft's ODBC Driver 18, in Docker on Linux; SQL Server through IIS has not been tested yet.

## Watching it

Add the status check to your monitoring, so a QGIS Server that starts without the plugin is noticed:

```powershell
C:\OSGeo4W\apps\Python312\python.exe C:\Groundwork\IIS\QgisServer\plugins\castlegate_server\tools\check.py https://<your server>/groundwork/qgisserver --map <a project>
```

The log is `C:\Groundwork\IIS\QgisServer\qgis_server.log`, with one audit line per save at Warning.
