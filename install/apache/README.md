# Castlegate Server behind Apache

**A guide, not yet tested.** The lines below follow QGIS's own Apache setup with `mod_fcgid`, from the QGIS Server manual, and add the four jobs Castlegate needs. Run the status check at the end before relying on it, and tell us what you changed to make it work.

It needs `mod_fcgid`, `mod_rewrite`, `mod_headers` and `mod_setenvif`.

```apache
<VirtualHost *:443>
    ServerName maps.example.org
    # ... your TLS settings ...

    # 3. The plugin's settings and its own plugin folder, for this QGIS Server alone.
    FcgidInitialEnv QGIS_PLUGINPATH /opt/castlegate/plugins
    FcgidInitialEnv CASTLEGATE_ISSUER https://login.microsoftonline.com/<tenant ID>/v2.0
    FcgidInitialEnv CASTLEGATE_AUDIENCE <application (client) ID>
    FcgidInitialEnv CASTLEGATE_SCOPE access_as_user
    FcgidInitialEnv CASTLEGATE_TENANT <tenant ID>
    # 4. The log, at Warning so the audit lines are kept.
    FcgidInitialEnv QGIS_SERVER_LOG_FILE /var/log/qgis/castlegate.log
    FcgidInitialEnv QGIS_SERVER_LOG_LEVEL 1

    ScriptAlias /groundwork/qgisserver /usr/lib/cgi-bin/qgis_mapserv.fcgi
    <Location /groundwork/qgisserver>
        SetHandler fcgid-script
        Options +ExecCGI
        Require all granted
    </Location>

    RewriteEngine on
    # 1. Apache does not pass Authorization to FastCGI by itself.
    RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]

    # 2. Groundwork's origin: the cross-origin headers, and the preflight answered here,
    #    because QGIS Server does not answer OPTIONS. Another origin gets none of it.
    SetEnvIf Origin "^https://app\.groundwork\.do$" CASTLEGATE_ORIGIN=$0
    Header always set Access-Control-Allow-Origin "%{CASTLEGATE_ORIGIN}e" env=CASTLEGATE_ORIGIN
    Header always set Access-Control-Allow-Headers "Authorization, Content-Type" env=CASTLEGATE_ORIGIN
    Header always set Access-Control-Allow-Methods "GET, POST, OPTIONS" env=CASTLEGATE_ORIGIN
    Header always set Access-Control-Expose-Headers "WWW-Authenticate" env=CASTLEGATE_ORIGIN
    Header always merge Vary Origin
    RewriteCond %{REQUEST_METHOD} =OPTIONS
    RewriteCond %{ENV:CASTLEGATE_ORIGIN} !^$
    RewriteRule ^/groundwork/qgisserver - [R=204,L]
    RewriteCond %{REQUEST_METHOD} =OPTIONS
    RewriteRule ^/groundwork/qgisserver - [F,L]
</VirtualHost>
```

`/opt/castlegate/plugins` holds `castlegate_server/` from the release zip, and nothing else needs to be in it. Never use QGIS's own plugin folder, which every QGIS Server on the machine searches ([`install/README.md`](../README.md)).

If the machine already runs a QGIS Server under Apache, give Castlegate's its own `VirtualHost` or its own FastCGI wrapper, so its settings and plugin folder reach only it. `FcgidInitialEnv` applies to every FastCGI program its context starts.

Then run the status check:

```bash
python /opt/castlegate/plugins/castlegate_server/tools/check.py https://maps.example.org/groundwork/qgisserver --map /srv/projects/<a project>.qgs --origin https://app.groundwork.do
```
