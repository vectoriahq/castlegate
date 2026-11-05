# Castlegate Server behind nginx

**A guide, not yet tested on its own.** The nginx lines are the ones the Docker profile runs, which the plugin's tests check; the process manager around them is not tested. Run the status check at the end before relying on it, and tell us what you changed to make it work.

nginx does not start QGIS Server, so this has two parts: a service that runs QGIS Server with the plugin's settings, and the nginx lines in front of it.

## 3 and 4: running QGIS Server with the settings

A systemd service running QGIS Server through `spawn-fcgi`, as the `qgis/qgis-server` image does, at `/etc/systemd/system/castlegate-qgis.service`:

```ini
[Unit]
Description=QGIS Server with Castlegate Server
After=network.target

[Service]
User=www-data
Group=www-data
Environment=QGIS_PLUGINPATH=/opt/castlegate/plugins
Environment=CASTLEGATE_ISSUER=https://login.microsoftonline.com/<tenant ID>/v2.0
Environment=CASTLEGATE_AUDIENCE=<application (client) ID>
Environment=CASTLEGATE_SCOPE=access_as_user
Environment=CASTLEGATE_TENANT=<tenant ID>
# The log goes to the journal, at Warning so the audit lines are kept.
Environment=QGIS_SERVER_LOG_STDERR=1
Environment=QGIS_SERVER_LOG_LEVEL=1
Environment=QT_QPA_PLATFORM=offscreen
ExecStart=/usr/bin/spawn-fcgi -n -a 127.0.0.1 -p 9993 -- /usr/lib/cgi-bin/qgis_mapserv.fcgi
# An exception escaping a plugin ends QGIS Server; this starts it again.
Restart=always

[Install]
WantedBy=multi-user.target
```

`/opt/castlegate/plugins` holds `castlegate_server/` from the release zip. Never use QGIS's own plugin folder, which every QGIS Server on the machine searches ([`install/README.md`](../README.md)).

## 1 and 2: the nginx lines

In the `server` block:

```nginx
# 2. The preflight and cross-origin headers for Groundwork's origin.
include /etc/nginx/castlegate-cors.conf;

location /groundwork/qgisserver {
    fastcgi_pass  127.0.0.1:9993;
    fastcgi_param SCRIPT_FILENAME /usr/lib/cgi-bin/qgis_mapserv.fcgi;
    fastcgi_param QUERY_STRING    $query_string;
    # 1. fastcgi_params, with nginx's default of passing every request header as an
    #    HTTP_ variable, brings Authorization through as HTTP_AUTHORIZATION.
    include fastcgi_params;
}
```

`castlegate-cors.conf` is the Docker profile's file, [`install/docker/castlegate-cors.conf`](../docker/castlegate-cors.conf), copied to `/etc/nginx/`. It belongs in the `server` block, not in a `location`, and it allows `https://app.groundwork.do`; change the origin on its first `if` line for another. If a `location` in front of QGIS Server sets any `add_header` of its own, nginx drops the server block's, so repeat them there.

Then run the status check:

```bash
python3 /opt/castlegate/plugins/castlegate_server/tools/check.py https://maps.example.org/groundwork/qgisserver --map /srv/projects/<a project>.qgs --origin https://app.groundwork.do
```
