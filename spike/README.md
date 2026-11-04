# Castlegate spike

The prototype from package 1 of Groundwork's plan for per-person access to QGIS Server layers (`docs/plans/qgis-server-access-control.md` in `vectoriahq/groundwork`, whose § "Package 1, first part: findings, 2026-11-04" records what it found). It is a prototype to learn from, not the plugin package 2 builds.

- `plugin/castlegate_spike/`: the plugin. `token.py` checks an RS256 token with Python's own `pow()` and `hashlib`; `rules.py` combines the rows that name a person; `plugin.py` holds the access-control filter, the request filter and the status service.
- `rules.json`: the test rules. The harness adds a read row per layer of the 100-layer project.
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
