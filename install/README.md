# Installing Castlegate Server

The plugin is the same on every web server. It reads its settings from environment variables, because that is the one channel every web server in front of QGIS Server offers. What differs by web server is the front door, and only four jobs:

1. passing the `Authorization` header through to QGIS Server;
2. answering the browser's preflight request for Groundwork's origin, and letting the browser read `WWW-Authenticate` on a refusal;
3. setting the plugin's environment variables and `QGIS_PLUGINPATH`;
4. running QGIS Server, restarting it, and keeping its log.

Each folder here does those four jobs for one web server, and each ends with the same status check.

| Folder | State |
| --- | --- |
| [`common/`](common/README.md) | The status check every profile ends with |
| [`docker/`](docker/README.md) | Complete; the plugin's own tests run on it |
| [`apache/`](apache/README.md) | A guide, not yet tested |
| [`nginx/`](nginx/README.md) | A guide, not yet tested |

## The settings

| Variable | Holds |
| --- | --- |
| `CASTLEGATE_ISSUER` | The sign-in provider's issuer. For Entra: `https://login.microsoftonline.com/<tenant ID>/v2.0` |
| `CASTLEGATE_AUDIENCE` | The application (client) ID of the app registration the tokens are for, which is what a version 2 Entra token's `aud` holds |
| `CASTLEGATE_SCOPE` | The scope the app registration exposes, such as `access_as_user` |
| `CASTLEGATE_TENANT` | Optional. When set, a token's `tid` must equal it |
| `CASTLEGATE_EMAIL_CLAIM` | Optional. The claim holding the sign-in email; `preferred_username` by default |
| `CASTLEGATE_GROUPS_CLAIM` | Optional. The claim holding group object IDs; `groups` by default |
| `CASTLEGATE_ID_CLAIM` | Optional. The claim holding the person's immutable ID; `oid` by default |
| `QGIS_PLUGINPATH` | A folder of this QGIS Server's own that holds `castlegate_server/` |

Until `CASTLEGATE_ISSUER`, `CASTLEGATE_AUDIENCE` and `CASTLEGATE_SCOPE` are all set, every sign-in is refused, and the server log says which is missing. Set the app registration to issue version 2 access tokens, so the issuer has the one form above.

**Never put the plugin in OSGeo4W's own plugin folder.** QGIS Server searches that folder for every QGIS Server on the machine, so a copy there loads into every one of them and demands sign-in of them all. Use a folder of its own, named in `QGIS_PLUGINPATH` for this QGIS Server alone.

## The project

Each project the server opens names its rules layer in the project variable `castlegate_rules_layer`. The table's columns are in [`docs/rules-table.md`](../docs/rules-table.md), and what the plugin does not protect is in [`docs/limits.md`](../docs/limits.md).
