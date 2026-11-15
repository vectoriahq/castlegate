"""The rules of the project QGIS Server opened, read from a table added to that project.

The project names its rules table, as it appears in the Layers panel, in the
project variable `castlegate_rules_table`, so the table rides the project's own data connections
and each project on a server keeps its own rules. Each worker process keeps its
own copy per project and reads the table again once the copy is
`MAX_AGE_SECONDS` old, because a database table has no stamp to watch.
"""

import time
from dataclasses import dataclass, field

from qgis.core import QgsExpression, QgsExpressionContextUtils, QgsMapLayer, QgsVectorLayer

from .core import rules as core_rules
from .core.tokens import Person

VARIABLE = "castlegate_rules_table"
MAX_AGE_SECONDS = 30

# Stand-in values for checking that a filter parses once the placeholders are filled in.
_PROBE = Person(id="probe", email="probe@example.invalid", groups=frozenset())


@dataclass
class ProjectRules:
    path: str
    layer_id: str = ""
    layer_name: str = ""
    rules: object = None  # a core.rules.Rules once read
    rows: int = 0
    problems: list = field(default_factory=list)
    error: str = ""
    read_at: float = 0.0  # time.time() of the read
    _read_monotonic: float = 0.0

    @property
    def loaded(self):
        return self.rules is not None


def _value(v):
    """A plain Python value from a QGIS attribute: QGIS 3 gives NULL as a null QVariant, QGIS 4 as None."""
    if v is None:
        return None
    is_null = getattr(v, "isNull", None)
    if callable(is_null) and type(v).__name__ == "QVariant":
        return None if is_null() else v.value()
    return v


def _check_expressions(row):
    for column in ("read_filter", "edit_filter"):
        text = getattr(row, column)
        if text:
            probe = QgsExpression(core_rules.substitute(text, _PROBE, QgsExpression.quotedString))
            if probe.hasParserError():
                raise core_rules.RowError("%s is not a valid QGIS expression: %s" % (column, probe.parserErrorString()))


def read(project):
    """ProjectRules for project, read now. Never raises; a failure is recorded in `error`."""
    found = ProjectRules(path=project.fileName() if project else "")
    found.read_at = time.time()
    found._read_monotonic = time.monotonic()
    try:
        if project is None:
            found.error = "no project was opened"
            return found
        name = QgsExpressionContextUtils.projectScope(project).variable(VARIABLE)
        name = _value(name)
        if not isinstance(name, str) or not name.strip():
            found.error = "the project has no %s variable naming its rules table" % VARIABLE
            return found
        found.layer_name = name.strip()
        layers = project.mapLayersByName(found.layer_name)
        if len(layers) != 1:
            found.error = "the project has %d layers named %s, not one" % (len(layers), found.layer_name)
            return found
        layer = layers[0]
        found.layer_id = layer.id()
        if not isinstance(layer, QgsVectorLayer) or layer.type() != QgsMapLayer.LayerType.VectorLayer or not layer.isValid():
            found.error = "the rules table %s cannot be read" % found.layer_name
            return found
        names = layer.fields().names()
        missing = [c for c in ("who_type", "who", "layer") if c not in names]
        if missing:
            found.error = "the rules table has no %s column" % ", ".join(missing)
            return found
        columns = [c for c in core_rules.COLUMNS if c in names]
        records = []
        for feature in layer.getFeatures():
            records.append({c: _value(feature[c]) for c in columns})
        rows, problems = [], []
        for number, record in enumerate(records, start=1):
            try:
                row = core_rules.parse_row(record)
                _check_expressions(row)
                rows.append(row)
            except core_rules.RowError as e:
                problems.append("row %d: %s" % (number, e))
        found.rules = core_rules.Rules(rows)
        found.rows = len(rows)
        found.problems = problems
    except Exception as e:  # noqa: BLE001 - any failure leaves the project with no rules
        found.rules = None
        found.error = "the rules table could not be read (%s)" % type(e).__name__
    return found


class Cache:
    """ProjectRules per project file, re-read once MAX_AGE_SECONDS old."""

    def __init__(self, now=time.monotonic):
        self._by_path = {}
        self._now = now

    def get(self, project):
        path = project.fileName() if project else ""
        held = self._by_path.get(path)
        if held is None or self._now() - held._read_monotonic >= MAX_AGE_SECONDS:
            held = read(project)
            self._by_path[path] = held
        return held
