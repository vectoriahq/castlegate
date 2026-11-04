# Castlegate spike

The prototype from package 1 of Groundwork's plan for per-person access to QGIS Server layers (`docs/plans/qgis-server-access-control.md` in `vectoriahq/groundwork`, whose § "Package 1, first part: findings, 2026-11-04" records what it found). It is a prototype to learn from, not the plugin package 2 builds.

- `plugin/castlegate_spike/`: the plugin. `token.py` checks an RS256 token with Python's own `pow()` and `hashlib`; `rules.py` combines the rows that name a person; `plugin.py` holds the access-control filter, the request filter and the status service.
- `rules.json`: the test rules. The harness adds a read row per layer of the 100-layer project.
- `mssql/Dockerfile`: the QGIS Server image with Qt's ODBC plugin and Microsoft's ODBC Driver 18 for SQL Server added, for the SQL Server run.
- `build_project.py`: builds the test GeoPackage and two projects inside the QGIS Server image.
- `run.mjs`: starts a stub OpenID Connect issuer and QGIS Server in containers, mints tokens with a test key, and prints one line per check.

Run it with Node.js and Docker running:

```bash
docker pull qgis/qgis-server:ltr
```

```bash
node spike/run.mjs <work folder>
```

It uses port 8020 and the container names `castlegate-spike` and `castlegate-issuer`, and removes both when it finishes. The work folder gets the built data, the logs and `results.json`.

To run the same checks with the layers in SQL Server, set `CASTLEGATE_BACKEND=mssql`. The harness then builds the image in `mssql/` and starts Microsoft's SQL Server 2022 Developer container as `castlegate-mssql`, which it also removes. Both accept Microsoft's licence terms: [ODBC Driver 18](https://aka.ms/odbc18eula) and [SQL Server 2022 Developer](https://go.microsoft.com/fwlink/?linkid=857698).

```bash
docker pull mcr.microsoft.com/mssql/server:2022-latest
```

```bash
CASTLEGATE_BACKEND=mssql node spike/run.mjs <work folder>
```
