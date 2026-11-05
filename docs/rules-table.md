# The rules table

The rules say who may see and edit what. They are a table the administrator edits in QGIS, kept wherever QGIS can read it: SQL Server, PostGIS, or a GeoPackage beside the project. `core/rules.py` reads it.

## Columns

| Column | Holds |
| --- | --- |
| `kind` | `person` or `group` |
| `who` | for a person, their sign-in email or their object ID; for a group, its object ID |
| `layer` | the name the project publishes the layer under |
| `can_see`, `can_add`, `can_change`, `can_move`, `can_delete` | yes or no. `can_change` covers a feature's details, `can_move` its shape |
| `see_filter` | a QGIS expression choosing the features they see; empty for every feature |
| `edit_filter` | a QGIS expression choosing the features they may add, change, move or delete; empty for every feature |
| `hidden_fields` | fields whose values they cannot see, separated by commas |
| `readonly_fields` | fields they see but cannot set when adding or changing a feature, separated by commas |

A yes-or-no column takes true or false, 1 or 0, or the words yes, no, true or false. An empty one means no. Emails and object IDs are matched whatever their capitals; layer and field names exactly.

In a filter, `{{email}}` and `{{oid}}` stand for the signed-in person's email and object ID, for example `"owner" = {{email}}`. Castlegate puts the value in quotes itself. A filter that uses `{{email}}` for a person whose sign-in carries no email matches no features.

## How rows combine

A person may do anything any of their rows allows. There are no rows that take a right away.

- **Rights** add up across rows.
- **Filters** from several rows combine with OR, and a row with an empty filter lets the person see, or edit, every feature.
- **A field is hidden** only where every row that lets the person see the layer hides it, and **read-only** only where every row that lets them add or change features makes it read-only or hidden.
- **A row counts only for what it grants.** A row that lets someone delete but not see does not widen which features or fields they see.

## Rows that cannot be read

A row with an unknown `kind`, an empty `who` or `layer`, a yes-or-no column holding something else, or a placeholder other than `{{email}}` and `{{oid}}` is left out, and its problem is reported. Leaving a row out can only take rights away, because every row only grants.

## Sign-in email

The email is read from the token's `preferred_username` claim by default. Microsoft calls that value mutable: a row naming an email applies to whichever account has that name now. For sensitive layers, name the person by object ID, or name a group.
