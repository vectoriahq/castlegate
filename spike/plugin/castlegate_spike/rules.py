"""Rules for the spike, read from a JSON file named by CASTLEGATE_RULES.

Each row: who (email, oid or group), layer (published name), may (a list of
read, insert, update, move, delete), view (expression or null), edit
(expression or null), hidden (fields), readonly (fields). Matching rows combine
with the most permissive answer winning. Package 2 reads the rows from a
project layer instead.
"""

import json
import os

from qgis.core import QgsExpression


class Rules:
    def __init__(self, path=None):
        self.path = path or os.environ.get("CASTLEGATE_RULES", "")
        self.rows = []
        self.stamp = None

    def _load(self):
        # Re-read when the file's stamp changes; each worker process keeps its own copy.
        stamp = os.stat(self.path).st_mtime_ns
        if stamp != self.stamp:
            with open(self.path, encoding="utf-8") as f:
                self.rows = json.load(f)["rows"]
            self.stamp = stamp

    def answer(self, person, layer_name):
        self._load()
        rows = []
        for row in self.rows:
            if row["layer"] != layer_name:
                continue
            who = row["who"]
            if (
                ("email" in who and who["email"].lower() == person["email"])
                or ("oid" in who and who["oid"] == person["oid"])
                or ("group" in who and who["group"] in person["groups"])
            ):
                rows.append(row)
        return combine(rows, person)


def _expr(text, person):
    if not text:
        return None
    return text.replace("{{email}}", QgsExpression.quotedValue(person["email"])).replace(
        "{{oid}}", QgsExpression.quotedValue(person["oid"])
    )


def combine(rows, person):
    """The person's answer for one layer, from every row that names them."""
    if not rows:
        return {"any": False, "may": set(), "view": "FALSE", "edit": "FALSE", "hidden": None, "blocked": None}
    may = set()
    views, edits = [], []
    view_all = edit_all = False
    hidden = blocked = None
    for row in rows:
        may.update(row.get("may", []))
        v = _expr(row.get("view"), person)
        if v is None:
            view_all = True
        else:
            views.append(v)
        e = _expr(row.get("edit"), person)
        if e is None:
            edit_all = True
        else:
            edits.append(e)
        row_hidden = set(row.get("hidden", []))
        row_blocked = row_hidden | set(row.get("readonly", []))
        # A field stays hidden, or blocked, only where every matching row says so.
        hidden = row_hidden if hidden is None else hidden & row_hidden
        blocked = row_blocked if blocked is None else blocked & row_blocked
    return {
        "any": True,
        "may": may,
        "view": None if view_all else " OR ".join("(%s)" % v for v in views),
        "edit": None if edit_all else " OR ".join("(%s)" % e for e in edits),
        "hidden": hidden,
        "blocked": blocked,
    }
