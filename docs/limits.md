# What Castlegate Server does not do

Castlegate Server decides, for each signed-in person, which layers, features and fields QGIS Server's web services give them, and which saves those services accept. These are the limits of that.

## What it protects

- **The web services, not the database.** Someone who connects to the database directly, with a database login, is not covered. QGIS desktop and QField can sign in to a WFS service with OAuth2, so office editing can move onto the protected service and database logins can be taken away.
- **WMS and WFS only.** Every other service is refused, OGC API Features, WCS and WMTS included, and so is any service a later QGIS adds, until Castlegate learns it.
- **Only while it is loaded.** If QGIS Server starts without the plugin, after an upgrade that breaks it for example, QGIS Server logs the failure and serves everything to anyone who can reach it. The status check in [`install/common/`](../install/common/README.md) tells the two apart; add it to your monitoring.

## What a person can still learn

- **A hidden field's name and type.** Its values are left out, but GetProjectSettings and WFS DescribeFeatureType still list the field, because QGIS Server does not ask Castlegate about them.
- **That a layer they may not edit can be edited.** WFS GetCapabilities lists Insert, Update and Delete from the project's settings alone, not from the person's rights. A save they may not make is refused when they make it.

## Sign-in

- **A row naming an email follows whoever has that email now.** The email is read from the token's `preferred_username` claim by default, which Microsoft calls mutable and says not to use for authorization. If an address is reused, the new holder gets the old holder's rows. For sensitive layers, name the person by object ID, or name a group. The audit line records the object ID beside the email.
- **A person in too many groups is refused.** Entra leaves the `groups` claim out of a token once a person is in more than 200 groups, and Castlegate refuses such a token rather than guess, with a status message saying why.
- **Signing keys are fetched over HTTPS only**, from the issuer's OpenID Connect discovery document. A QGIS Server process that has not yet reached the issuer refuses every sign-in; one that has keeps using the keys it holds when a later fetch fails, and refuses a token signed with a key it does not hold.

## Saves

- **A refused save shows QGIS Server's own message,** "No permissions to do WFS changes on layer …", with no reason. Castlegate decides on a save once QGIS Server has opened the project, when it can no longer stop the save itself, so it refuses through its answers to QGIS Server instead. The reason goes to the server log and the audit line.
- **A change that takes a feature out of the person's edit scope is refused.** QGIS Server checks the feature before the change and again after it.
- **One save making several kinds of edit to a layer must pass each kind's edit filter** for every feature it touches, because QGIS Server does not say which kind of edit it is checking.
- **The database sees one account.** Who changed what is in Castlegate's audit line in QGIS Server's log, not in the database.

## Speed

- **Capabilities are built again on every request.** QGIS Server keeps one cached copy of a project's GetCapabilities and GetProjectSettings for everyone, which would serve one person's layers to the next, so Castlegate turns that cache off for its requests. On a test project of 100 layers, GetProjectSettings took about 320 ms without the cache against 13 ms with it.
- **A filter QGIS cannot turn into SQL reads every feature.** A rule written as a plain comparison, such as `"owner" = {{email}}`, is sent to the database as SQL. One using a function the database cannot run, such as `regexp_match`, makes QGIS Server read the whole layer and filter it itself.
- **A rules change takes up to 30 seconds to apply.** Each QGIS Server process reads the rules again once its copy is that old.

## Versions

Castlegate Server needs QGIS Server 3.36 or later. Its tests run on the long-term release and the latest release of the `qgis/qgis-server` Docker image.
