# Castlegate

Per-person access control for QGIS Server. An administrator decides who may see and edit which layers, features and fields; the server checks each person's OpenID Connect sign-in and answers QGIS Server's access-control questions from those rules.

The design and its decisions are in Groundwork's plan, `docs/plans/qgis-server-access-control.md` in `vectoriahq/groundwork`. This repository is private until licensing advice has been taken; the plan records the intended licence, GPL version 2 or later.

## What is here

- `core/`: what every Castlegate product shares, in plain Python with no QGIS: the rules table's shape (`docs/rules-table.md`), the sign-in check that turns a token into a person and their groups, and how the rows naming a person combine.
- `server/castlegate_server/`: Castlegate Server, the QGIS Server plugin. `scripts/build.py` copies `core/` into it and makes the folder and zip that are installed:

  ```bash
  python scripts/build.py
  ```

- `install/`: how to set it up behind each web server, and the status check every setup ends with ([`install/README.md`](install/README.md)). What it does not protect is in [`docs/limits.md`](docs/limits.md).
- `tests/core/` and `tests/plugin/`: tests of `core/` and of the plugin's save-request reader, in plain Python 3.12 or later:

  ```bash
  python -m unittest discover -s tests/core -t .
  ```

  ```bash
  python -m unittest discover -s tests/plugin -t .
  ```

- `tests/server/`: the plugin's tests against a real QGIS Server, which need Node.js and Docker. They build the plugin and the test projects, start a stub sign-in issuer and QGIS Server in containers on port 8020, and print one line per check. CI runs them on `qgis/qgis-server:ltr` and `:stable`:

  ```bash
  node tests/server/run.mjs --image qgis/qgis-server:ltr
  ```

  With `CASTLEGATE_BACKEND=mssql` the layers and rules tables are in SQL Server instead of a GeoPackage, through Microsoft's ODBC Driver 18 and SQL Server 2022 Developer, whose licence terms running them accepts ([ODBC Driver 18](https://aka.ms/odbc18eula), [SQL Server 2022 Developer](https://go.microsoft.com/fwlink/?linkid=857698)). CI runs that weekly and on request (`.github/workflows/sql-server.yml`).

- `spike/`: the package 1 prototype and the harness that runs its checks against the Docker QGIS Server.
