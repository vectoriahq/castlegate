# The rules table

The rules say who may read and edit what. They are a table with no geometry that the GIS administrator edits in QGIS, kept wherever QGIS can read it: SQL Server, PostGIS, or a GeoPackage beside the project. `core/rules.py` reads it.

## Adding it to a project

The table is added to the project, so it shows in the Layers panel, and the project variable `castlegate_rules_table` (Project Properties, Variables, in QGIS) holds its name as the Layers panel shows it. The variable is also how anyone opening the project later can tell which table Castlegate reads.

Call the table whatever suits your projects; we suggest `access_rules`. Its name in the Layers panel may differ from its name in the database, such as `Access Rules`, as long as the variable holds the Layers panel's name.

Each project on a server has its own rules, read through the project's own data connections. Castlegate Server never serves the rules table, whatever the project's publishing settings say. It serves nothing from a project with no such variable, whose rules table cannot be read, or that has more than one layer with that name. Each QGIS Server process reads the rules again once its copy is 30 seconds old.

## Columns

| Column | Holds |
| --- | --- |
| `who_type` | `user` or `group` |
| `who` | for a user, their sign-in email or their object ID; for a group, its object ID ([Naming a user or a group](#naming-a-user-or-a-group)) |
| `layer` | the name the project publishes the layer under |
| `can_read` | yes or no: read the layer's features and their attributes |
| `can_insert` | yes or no: insert features |
| `can_update_attributes` | yes or no: update a feature's attributes |
| `can_update_geometry` | yes or no: update a feature's geometry |
| `can_delete` | yes or no: delete features |
| `read_filter` | a QGIS expression choosing the features they may read; empty for every feature |
| `edit_filter` | a QGIS expression choosing the features they may insert, update or delete; empty for every feature |
| `hidden_fields` | fields whose values they cannot read, separated by commas, such as `cost, owner_phone` |
| `readonly_fields` | fields they can read but cannot set when inserting a feature or updating its attributes, separated by commas the same way |

A yes-or-no column takes true or false, 1 or 0, or the words yes, no, true or false, so it can be a text, integer or boolean column. An empty one means no. Spaces around a field name in `hidden_fields` and `readonly_fields` are ignored. Layer and field names are matched exactly.

In a filter, `{{email}}` and `{{oid}}` stand for the signed-in user's email and object ID, for example `"owner" = {{email}}`. Castlegate puts the value in quotes itself. A filter that uses `{{email}}` for a user whose sign-in carries no email matches no features.

## Naming a user or a group

| `who_type` | `who` | Example | Where to find it |
| --- | --- | --- | --- |
| `user` | their sign-in email | `ana.lopez@council.example` | the name they sign in with |
| `user` | their object ID | `9d2f4c7a-61e3-4b8d-a0f5-3c7e2b9d1a46` | Microsoft Entra admin center, Users, the user's Overview |
| `group` | its object ID | `5b8e2a14-3c7f-4d91-a6e0-8f2b7c3d9e41` | Microsoft Entra admin center, Groups, the group's Overview |

The examples are made up. An email matches whatever the case of its letters A to Z, and so does an ID written as a GUID, as Entra's are; any other ID must match exactly.

A group is named by its object ID, because that is what Entra puts in a sign-in token, and a row naming a group by an email is left out. The app registration adds groups to its tokens only once its groups claim is turned on ([`install/iis/README.md`](../install/iis/README.md)).

With an identity provider other than Entra, the claims Castlegate reads the email, the ID and the groups from are settings ([`install/README.md`](../install/README.md#the-settings)).

## How rows combine

A user may do anything any of their rows allows. There are no rows that take a right away, so a layer no row names is read by nobody, and a user no row names gets nothing.

- **Rights** add up across rows.
- **Each right is worked out on its own,** from the rows that grant that right.
- **Filters** from those rows combine with OR, and a row with an empty filter opens that right to every feature. A row's `edit_filter` applies to each edit it grants, separately: a row allowing deletes with no filter lets the user delete any feature, but changes nothing about which features they may update.
- **A field is hidden** only where every row that lets the user read the layer hides it. It is **read-only when inserting** only where every row granting `can_insert` makes it read-only or hidden, and the same for updating attributes with `can_update_attributes`.
- **A row counts only for what it grants.** A row that lets someone delete but not read does not widen which features or fields they read.
- **One WFS-T transaction making several kinds of edit to a layer,** such as an update and a delete together, must pass the `edit_filter` of each kind for every feature it touches, because QGIS Server does not say which kind of edit it is checking.

## Rows that cannot be read

A row with an unknown `who_type`, an empty `who` or `layer`, a yes-or-no column holding something else, or a placeholder other than `{{email}}` and `{{oid}}` is left out. Leaving a row out can only take rights away, because every row only grants. The status check, run with a token, lists each row left out and why ([`install/common/`](../install/common/README.md)).

## Sign-in email

The email is read from the token's `preferred_username` claim by default. Microsoft calls that value mutable: a row naming an email applies to whichever account has that name now. For sensitive layers, name the user by object ID, or name a group.
