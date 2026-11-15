# Castlegate Server in Docker

The `qgis/qgis-server` image with the plugin built in. The plugin's own tests run on this profile: they build this folder's `Dockerfile` on the image under test and run every check against it.

## What is here

- `Dockerfile`: the image, with the plugin folder at `/io/plugins/castlegate_server`, where the image's `QGIS_PLUGINPATH` points, and a health check that runs the status check.
- `castlegate-cors.conf`: the browser's preflight answer and cross-origin headers for `https://app.groundwork.do`, in the image's nginx. Another origin's preflight is refused with 403. To allow a different origin, change it on the file's first `if` line.
- `compose.yaml`: one QGIS Server with the settings, the projects folder and a restart policy.

## Setting it up

1. Unzip the release zip here, so this folder holds `castlegate_server/` beside the `Dockerfile`.
2. Put your projects and their data in `data/`. Each project names its rules table in the `castlegate_rules_table` project variable ([`docs/rules-table.md`](../../docs/rules-table.md)).
3. Make a `.env` file beside `compose.yaml`:

   ```text
   CASTLEGATE_TENANT=<your tenant ID>
   CASTLEGATE_AUDIENCE=<the app registration's application (client) ID>
   CASTLEGATE_SCOPE=access_as_user
   CASTLEGATE_CHECK_MAP=/io/data/<a project>.qgs
   ```

4. Start it:

   ```bash
   docker compose up -d --build
   ```

5. Check it, from this folder:

   ```bash
   python castlegate_server/tools/check.py http://localhost:8010/ows/ --map /io/data/<a project>.qgs --origin https://app.groundwork.do
   ```

`docker compose ps` shows the health check's result as `healthy` or `unhealthy`.

## How it does the four jobs

1. **The `Authorization` header** reaches QGIS Server as nginx passes every request header to FastCGI.
2. **The preflight** is answered by nginx, from `castlegate-cors.conf`; QGIS Server does not answer `OPTIONS`. The image's own `NGINX_CORS_ALLOW_ORIGIN` setting is not used, because it does not let the browser read `WWW-Authenticate`.
3. **The settings** are the `environment` entries in `compose.yaml`; the image already sets `QGIS_PLUGINPATH`.
4. **The process** is the image's single QGIS Server, which `restart: unless-stopped` starts again if it ends. Its log goes to `docker compose logs`, with the audit lines at Warning.

**Keep `/io/plugins` free of volumes.** A volume mounted there hides the plugin the image carries, and QGIS Server then starts without it.

## Layers in SQL Server

The image has QGIS's SQL Server provider but no driver for it. A project whose layers are in SQL Server needs Qt's ODBC plugin and Microsoft's ODBC Driver 18 added to the image, as `tests/server/mssql/Dockerfile` does for the tests, and its connections naming that driver through an ODBC data source. Installing the driver accepts [Microsoft's licence terms](https://aka.ms/odbc18eula).
