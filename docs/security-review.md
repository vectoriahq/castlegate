# The security review: a brief for the reviewer

Castlegate Server is to be reviewed by someone outside Vectoria before a client relies on it, and the report is to be available to clients. This page is the brief for that review: what Castlegate must hold to, who it guards against, where to read closest, and what is already known.

What the plugin does is in the [README](../README.md), the rules table is in [`rules-table.md`](rules-table.md), and what it does not protect is in [`limits.md`](limits.md). Anything on the limits page is known and accepted, unless the reviewer finds it is worse than the page says.

## What Castlegate must hold to

1. **A signed-in person gets only what their rules allow:** no layer, feature or field value beyond them, through any request QGIS Server answers.
2. **A save goes through only where their rules allow it:** the kind of edit, the features it touches before and after, and the fields it sets.
3. **A request with no valid sign-in gets nothing,** except the status answer's 401.
4. **One person's answers never reach another,** within a process or across the processes serving one server.
5. **A failure inside the plugin refuses.** The one way it fails open is QGIS Server starting without it, which the limits page states.

## Who it guards against

- **A signed-in member of staff** who wants more than their rules give them, and who can send any request they like with their own valid token.
- **Anyone who can reach the server** without a token, or with a token from another app, tenant or issuer.

Out of scope: the server's administrators, anyone with access to the database, and the sign-in provider itself.

## What to read

In scope: `core/`, `server/castlegate_server/`, and the parts of `install/` that decide who can reach or change the server: `install/iis/Install-Castlegate.ps1` (the folder rights and the CORS rule) and `install/docker/castlegate-cors.conf`. `scripts/build.py` copies `core/` into the plugin.

Read closest:

- **`server/castlegate_server/transaction.py` and `request_filter.py`.** The plugin reads each WFS save itself, because QGIS Server does not check a save against the fields a person may not set. Its reader must find at least every field and layer that QGIS Server's own reader (`src/server/services/wfs/qgswfstransaction.cpp`) acts on. Where the two could differ, the plugin is meant to read more, so a difference can only cause a refusal.
- **`server/castlegate_server/request_expressions.py`.** The plugin reads each filter, sort and XML body a request carries before QGIS Server runs it, and must read at least every field name and function QGIS would act on, by every route QGIS reads them. The pull request that added it took three rounds of review findings on exactly that: a field's alias, a body in UTF-16, and a name split by a comment.
- **`core/rsa.py`, `core/tokens.py` and `core/keys.py`.** The signature check uses only what Python ships, with no cryptography package, because none is in QGIS Server's Python on Windows or in its Docker image.
- **`server/castlegate_server/access_filter.py`.** QGIS Server keeps one copy of a project for every request, so its answers must change nothing on a layer, and must depend only on the person on the request in hand.

Not in scope: `spike/`, the package 1 prototype. The tests are not in scope, but are the quickest way to try something against a real QGIS Server.

## Running it

The README gives the commands. The unit tests need Python 3.12 or later. The tests against a real QGIS Server, `tests/server/run.mjs`, need Node.js and Docker. They start a stub sign-in issuer and QGIS Server in containers and print one line per check, so a new attack is one more `record(...)` in that file.

## What a first reading found

Before this review, Vectoria ran its own reading of the plugin and the core. It is not an independent review. What it changed:

- A failed refresh of the signing keys no longer keeps them for ever: after a week without a fetch that worked, every sign-in is refused.
- An answer from the issuer that is not a key set, or holds no RS256 key, no longer replaces the keys held.
- An email is lower-cased in A to Z only, so a letter such as the Kelvin sign can no longer pass for k.
- An id that is not a GUID is compared exactly, as OpenID Connect `sub` values are case-sensitive.
- A token part with unused trailing bits is refused, so a signature has one spelling.
- A request with no sign-in no longer makes the plugin read the project's rules.
- QGIS Server runs a request's own filter or sort without asking the plugin. A filter on a hidden field told the person its values by which features came back, and a filter calling a function such as `aggregate()` or `get_feature()` read layers the person may not see, the rules layer included. The plugin now refuses a filter or sort that names a hidden field or uses a function outside a short list, a WMS `FILTER` written as a condition, which is SQL for the database, and a style sent with a WMS request (`server/castlegate_server/request_expressions.py`, `limits.md`). The checks beginning `filter-`, `sort-`, `ogc-filter-`, `body-filter-` and `wms-` in `tests/server/run.mjs` cover each route found.

## Not yet checked

These are open, and are the places most worth the reviewer's time after the save reader:

- Any way a request can name a field or call a function that `request_expressions.py` does not read, and whether any function in its `FUNCTIONS` list reads beyond the feature being tested.
- WMS GetPrint, and what a print layout's tables and labels show.
- What GetLegendGraphic and GetStyles reveal beyond a style's own values.
- Several worker processes serving one server under load.
- Real Entra tokens, including a person in more groups than a token lists.
- QGIS 4 on Windows, and the Apache and nginx profiles, which are guides only.

## What we ask for

- Findings with how severe each is and how to reproduce it, ideally as a check in `tests/server/run.mjs` or a unit test.
- A second look at the fixes.
- A report Vectoria may give to clients.
