# What Castlegate Server does not do

Castlegate Server decides, for each signed-in user, which layers, features and fields QGIS Server's web services give them, and which WFS-T transactions those services accept. These are the limits of that.

## What it protects

- **The web services, not the database.** Someone who connects to the database directly, with a database login, is not covered. QGIS desktop and QField can sign in to a WFS service with OAuth2, so office editing can move onto the protected service and database logins can be taken away.
- **WMS and WFS only.** Every other service is refused, OGC API Features, WCS and WMTS included, and so is any service a later QGIS adds, until Castlegate learns it.
- **Only while it is loaded.** If QGIS Server starts without the plugin, after an upgrade that breaks it for example, QGIS Server logs the failure and serves everything to anyone who can reach it. The status check in [`install/common/`](../install/common/README.md) tells the two apart; add it to your monitoring.

## What a user can still learn

- **A hidden field's name and type.** Its values are left out, but GetProjectSettings and WFS DescribeFeatureType still list the field, because QGIS Server does not ask Castlegate about them.
- **A hidden field's values, through the layer's style.** If the project styles or labels a layer by a field, the map, the legend and the layer's GetStyles answer show that field's values to anyone who may read the layer. Style and label a layer only by fields everyone who reads it may read.
- **That a layer they may not edit can be edited.** WFS GetCapabilities lists Insert, Update and Delete from the project's settings alone, not from the user's rights. An edit they may not make is refused when they make it.

## Filters and sorts a request carries

QGIS Server runs a request's own filter or sort without asking Castlegate about it, so Castlegate checks each one first. A refused request is answered 403 with no reason; the reason goes to the server log.

- **A filter or sort that names a field hidden from the user, by its name or its alias, is refused,** whichever layer of the project hides it. So a field hidden on one layer cannot be filtered on in another layer that has a field of the same name. This covers WFS `EXP_FILTER`, `FILTER` and `SORTBY`, a request sent as XML, and WMS `FILTER`.
- **WMS `FILTER` may hold only OGC filters.** A `layer:condition` item is refused, whatever it names, because QGIS Server hands the condition to the database as SQL, which Castlegate cannot read as the database will.
- **A request body QGIS Server may read as XML must be one Castlegate can read,** or it is refused. Castlegate reads UTF-8 and UTF-16, so a body in UTF-32 is refused.
- **A filter may use only a short list of functions:** those for comparisons, geometry and feature identifiers, and a few that change text or numbers. Other QGIS functions can read other layers or evaluate text as an expression, so a filter using one is refused, and so is a filter using a variable. The list is `FUNCTIONS` in `server/castlegate_server/request_expressions.py`.
- **A style sent with a WMS request is refused,** in `SLD` or `SLD_BODY`, because a style can label features with any field.

## Sign-in

- **A row naming an email follows whoever has that email now.** The email is read from the token's `preferred_username` claim by default, which Microsoft calls mutable and says not to use for authorization. If an address is reused, the new holder gets the old holder's rows. For sensitive layers, name the user by object ID, or name a group. The audit line records the object ID beside the email.
- **A user in too many groups is refused.** Entra leaves the `groups` claim out of a token once a user is in more than 200 groups, and Castlegate refuses such a token rather than guess, with a status message saying why.
- **Signing keys are fetched over HTTPS only**, from the issuer's OpenID Connect discovery document. A QGIS Server process that has not yet reached the issuer refuses every sign-in. One that has keeps using the keys it holds when a later fetch fails, for up to a week after the last fetch that worked, and then refuses every sign-in until the issuer answers. It refuses a token signed with a key it does not hold.
- **A server that cannot reach the issuer is slow to refuse.** Each process asks the issuer again at most once a minute, and can wait 10 seconds or more for each of its two documents before giving up.

## WFS-T transactions

- **A refused transaction shows QGIS Server's own message,** "No permissions to do WFS changes on layer …", with no reason. Castlegate decides on a transaction once QGIS Server has opened the project, when it can no longer stop the transaction itself, so it refuses through its answers to QGIS Server instead. The reason goes to the server log and the audit line.
- **A transaction sent as URL parameters is refused.** QGIS Server also accepts a WFS delete written into the address; Castlegate reads only a transaction sent as an XML body.
- **An update that takes a feature out of what the user may edit is refused.** QGIS Server checks the feature before the change and again after it.
- **One WFS-T transaction making several kinds of edit to a layer must pass each kind's `edit_filter`** for every feature it touches, because QGIS Server does not say which kind of edit it is checking.
- **The database sees one account.** Who changed what is in Castlegate's audit line in QGIS Server's log, not in the database.

## Speed

- **Capabilities are built again on every request.** QGIS Server keeps one cached copy of a project's GetCapabilities and GetProjectSettings for everyone, which would serve one user's layers to the next, so Castlegate turns that cache off for its requests. On a test project of 100 layers, GetProjectSettings took about 320 ms without the cache against 13 ms with it.
- **A filter QGIS cannot turn into SQL reads every feature.** A rule written as a plain comparison, such as `"owner" = {{email}}`, is sent to the database as SQL. One using a function the database cannot run, such as `regexp_match`, makes QGIS Server read the whole layer and filter it itself.
- **A rules change takes up to 30 seconds to apply.** Each QGIS Server process reads the rules again once its copy is that old.

## Versions

Castlegate Server needs QGIS Server 3.36 or later. Its tests run on the long-term release and the latest release of the `qgis/qgis-server` Docker image.
