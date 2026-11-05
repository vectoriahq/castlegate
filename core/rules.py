"""The rules table's shape, and how the rows that name a person combine.

Each row names a person or a group, one published layer, what they may do there,
and optionally which features they see, which they may edit, and which fields
they cannot see or cannot change. There are no deny rows: a person may do
anything any of their rows allows (see `combine`).

A row that cannot be read exactly is left out, and its problem is reported.
Leaving a row out can only take rights away, never add them, because every row
only grants.
"""

import re
from dataclasses import dataclass

COLUMNS = (
    "kind",
    "who",
    "layer",
    "can_see",
    "can_add",
    "can_change",
    "can_move",
    "can_delete",
    "see_filter",
    "edit_filter",
    "hidden_fields",
    "readonly_fields",
)
RIGHTS = ("see", "add", "change", "move", "delete")
EDIT_RIGHTS = frozenset(("add", "change", "move", "delete"))
WRITE_RIGHTS = frozenset(("add", "change"))

_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0", ""}
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]*)\s*\}\}")
_PLACEHOLDERS = ("email", "oid")


class RowError(ValueError):
    pass


@dataclass(frozen=True)
class Row:
    kind: str  # "person" or "group"
    who: str  # lower case: an email or object ID for a person, an object ID for a group
    layer: str
    rights: frozenset
    see_filter: str  # "" means every feature
    edit_filter: str  # "" means every feature
    hidden: frozenset
    readonly: frozenset

    def names(self, person):
        if self.kind == "group":
            return self.who in person.groups
        if "@" in self.who:
            return bool(person.email) and self.who == person.email
        return self.who == person.id


def _text(record, name):
    value = record.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RowError("%s is not text" % name)
    return value.strip()


def _flag(record, name):
    value = record.get(name)
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise RowError("%s is not yes or no" % name)


def _fields(record, name):
    value = record.get(name)
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, str):
        items = value.split(",")
    else:
        raise RowError("%s is not a list of field names" % name)
    if not all(isinstance(item, str) for item in items):
        raise RowError("%s is not a list of field names" % name)
    return frozenset(item.strip() for item in items if item.strip())


def _filter(record, name):
    text = _text(record, name)
    for match in _PLACEHOLDER.finditer(text):
        if match.group(1) not in _PLACEHOLDERS:
            raise RowError("%s uses %s; only {{email}} and {{oid}} stand for the person" % (name, match.group(0)))
    if "{{" in _PLACEHOLDER.sub("", text) or "}}" in _PLACEHOLDER.sub("", text):
        raise RowError("%s has an unmatched {{ or }}" % name)
    return text


def parse_row(record):
    """A Row from one record of the table, a mapping of column name to value; or raise RowError."""
    kind = _text(record, "kind").lower()
    if kind not in ("person", "group"):
        raise RowError("kind is not person or group")
    who = _text(record, "who").lower()
    if not who:
        raise RowError("who is empty")
    if kind == "group" and "@" in who:
        raise RowError("a group is named by its object ID, not an email")
    layer = _text(record, "layer")
    if not layer:
        raise RowError("layer is empty")
    rights = frozenset(right for right in RIGHTS if _flag(record, "can_" + right))
    return Row(
        kind=kind,
        who=who,
        layer=layer,
        rights=rights,
        see_filter=_filter(record, "see_filter"),
        edit_filter=_filter(record, "edit_filter"),
        hidden=_fields(record, "hidden_fields"),
        readonly=_fields(record, "readonly_fields"),
    )


def parse_rows(records):
    """(rows, problems): the rows that could be read, and one message per row that could not."""
    rows, problems = [], []
    for number, record in enumerate(records, start=1):
        try:
            rows.append(parse_row(record))
        except RowError as e:
            problems.append("row %d: %s" % (number, e))
    return rows, problems


@dataclass(frozen=True)
class Answer:
    """What one person may do on one layer."""

    rights: frozenset
    # A QGIS expression limiting the features they see, "" for every feature, or "FALSE" when not granted.
    see_filter: str
    # For each of add, change, move and delete: a QGIS expression limiting the
    # features it applies to, "" for every feature, or "FALSE" when not granted.
    edit_filters: dict
    # Fields whose values they may not see.
    hidden: frozenset
    # For add and change, where granted: the fields they may not set, hidden ones and read-only ones.
    blocked: dict

    @property
    def nothing(self):
        return not self.rights

    def edit_filter(self, right):
        return self.edit_filters.get(right, "FALSE")

    def blocked_on(self, right):
        """The fields that may not be set on an add or a change, or None when that right is not granted."""
        return self.blocked.get(right)


NOTHING = Answer(
    rights=frozenset(),
    see_filter="FALSE",
    edit_filters={right: "FALSE" for right in sorted(EDIT_RIGHTS)},
    hidden=frozenset(),
    blocked={},
)


def substitute(expression, person, quote):
    """The expression with {{email}} and {{oid}} replaced by the person's values, quoted by `quote`.

    One pass over the expression, so a quoted value is never searched again for
    a placeholder: an email containing "{{oid}}" stays inside its quotes. An
    expression naming a value the person's token lacks, such as {{email}} with no
    email claim, becomes "FALSE" rather than comparing features against ''.
    """
    values = {"email": person.email, "oid": person.id}
    used = {m.group(1) for m in _PLACEHOLDER.finditer(expression)}
    if any(not values[name] for name in used):
        return "FALSE"
    return _PLACEHOLDER.sub(lambda m: quote(values[m.group(1)]), expression)


def _any_of(filters):
    """OR of the filters, or "" (every feature) when any one of them is ""."""
    if not filters or "" in filters:
        return ""
    unique = sorted(set(filters))
    return unique[0] if len(unique) == 1 else " OR ".join("(%s)" % f for f in unique)


def _all_of(sets):
    result = None
    for s in sets:
        result = s if result is None else result & s
    return result or frozenset()


def combine(rows, person, quote):
    """The Answer for one person from the rows naming them on one layer; the most permissive wins.

    A row widens only what it grants, and each right is combined over the rows
    granting that right alone. A row's see filter and hidden fields count only
    for seeing; its edit filter only for the edits it grants, each separately;
    its read-only fields only for an add if it grants add, and for a change if
    it grants change. So a row granting one thing cannot loosen the conditions
    another row put on something else.
    """
    if not rows:
        return NOTHING
    rights = frozenset().union(*(row.rights for row in rows))
    if not rights:
        return NOTHING

    def granting(right):
        return [row for row in rows if right in row.rights]

    def filter_for(right, column):
        matching = granting(right)
        if not matching:
            return "FALSE"
        return _any_of([substitute(getattr(row, column), person, quote) for row in matching])

    return Answer(
        rights=rights,
        see_filter=filter_for("see", "see_filter"),
        edit_filters={right: filter_for(right, "edit_filter") for right in sorted(EDIT_RIGHTS)},
        hidden=_all_of(row.hidden for row in granting("see")),
        blocked={
            right: _all_of(row.hidden | row.readonly for row in granting(right))
            for right in sorted(WRITE_RIGHTS & rights)
        },
    )


class Rules:
    """A rules table, read once, answering for a person and a layer."""

    def __init__(self, rows):
        self._by_layer = {}
        for row in rows:
            self._by_layer.setdefault(row.layer, []).append(row)

    def answer(self, person, layer, quote):
        matching = [row for row in self._by_layer.get(layer, []) if row.names(person)]
        return combine(matching, person, quote)
