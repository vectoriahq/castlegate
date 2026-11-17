# Releasing Castlegate Server

1. Set the new version in `server/castlegate_server/metadata.txt` and in `VERSION` in `server/castlegate_server/status.py`, and merge it to `main`. `tests/plugin/test_build.py` fails while the two differ.
2. Publish a GitHub release from `main` whose tag is the plugin's folder name and that version, such as `castlegate_server@0.1.0`.
3. The [Build the QGIS plugin](.github/workflows/plugin-release.yml) workflow checks the tag against the version, runs the core and plugin tests, builds the zip with `scripts/build.py` and attaches it to the release.
4. Upload that zip at [plugins.qgis.org](https://plugins.qgis.org/plugins/add/).

The plugin is marked experimental in `metadata.txt`, so QGIS's Plugin Manager lists it only where **Show also experimental plugins** is ticked.

## The QGIS plugin repository's checks

The repository scans each upload with Bandit and detect-secrets, and blocks a version with any finding. `server/castlegate_server/.bandit`, which the zip carries beside `metadata.txt`, skips the one finding whose reason it gives; the repository then lists the version as "Validated (configured)". It also runs a Qt6 check, which does not block a version.
