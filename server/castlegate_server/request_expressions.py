"""The filters and sorts a request carries, checked before QGIS Server runs them.

QGIS Server runs a request's own filter or sort without asking the access
filter about it. So a filter on a hidden field tells the person its values by
which features come back, and a filter calling a function such as
`aggregate()` or `get_feature()` reads other layers, the rules layer
included. The plugin refuses a request whose filter or sort names a field the
person may not see on any layer of the project, uses a function outside
`FUNCTIONS`, or uses a variable.

What is read, following QGIS Server's own readers:

- WFS `EXP_FILTER`: QGIS expressions, split at every `;`
  (QgsServerParameterDefinition::toExpressionList).
- WFS `SORTBY`: a list split like QgsWfsParameter::toStringListWithExp, each
  item split at `,` with its order suffix taken off: the expression QGIS
  orders by (qgswfsgetfeature.cpp).
- WFS `FILTER` and WMS `FILTER`: OGC filters, and for WMS also
  `layer:condition` items, whose condition is SQL that QGIS hands to the data
  provider after its own token check (QgsWmsParameters::layerFilters).
- An XML body.
- WMS `SLD` and `SLD_BODY`: refused whatever they hold, because a style sent
  with the request can label features with any field.

An OGC filter or XML body is read for every `PropertyName` and
`ValueReference` in it, wherever it stands, and every `Function`. That reads
more than QGIS acts on, which can only refuse more. A part that looks like XML
but is not well-formed is refused.

The splitting and the XML reading are plain Python, tested without QGIS;
checking an expression uses QGIS's own parser.
"""

import re

from .transaction import TransactionError, _local, _parse

# Functions a filter or sort may use: those QGIS's OGC filter reader writes for
# the standard comparison, spatial and identifier filters, and some that read
# only the feature being tested. Anything else is refused, because some QGIS
# functions read other layers or evaluate text as an expression.
FUNCTIONS = frozenset(
    {
        "$geometry",
        "$id",
        "geom_from_gml",
        "geom_from_wkt",
        "intersects_bbox",
        "intersects",
        "disjoint",
        "touches",
        "crosses",
        "contains",
        "overlaps",
        "within",
        "equals",
        "distance",
        "lower",
        "upper",
        "coalesce",
        "to_int",
        "to_real",
        "to_string",
        "to_date",
        "to_datetime",
    }
)

# QgsWfsParameter::toStringListWithExp's default pattern (qgswfsparameters.h).
_GROUPS = re.compile(r"\(([^()]+)\)")
# The order suffixes qgswfsgetfeature.cpp takes off a SORTBY item, in its order.
_SORT_SUFFIXES = (" D", "+D", " DESC", "+DESC", " A", "+A", " ASC", "+ASC")


class Refused(ValueError):
    pass


def groups(value):
    """QgsWfsParameter::toStringListWithExp: the text of each innermost (...) group, or the whole value."""
    if not value:
        return []
    return _GROUPS.findall(value) or [value]


def semicolon_list(value):
    """QgsServerParameterDefinition::toExpressionList, without its OGC special case: the parts between semicolons."""
    return [part for part in value.split(";") if part] if value else []


def ogc_filter_list(value):
    """QgsServerParameterDefinition::toOgcFilterList."""
    pos, found = 0, []
    while pos < len(value):
        if pos + 1 < len(value) and value[pos] == "(" and value[pos + 1] == "<":
            end = value.find("Filter>)", pos)
            if end < 0:
                end = len(value)
            found.append(value[pos + 1 : end + 7])
            pos = end + 8
        elif pos + 1 < len(value) and value[pos] == "(" and value[pos + 1] == ")":
            found.append("")
            pos += 2
        elif value[pos] == "<" and pos + 7 < len(value) and value[pos + 1 : pos + 7] == "Filter":
            found.append(value[pos:])
            break
        else:
            pos += 1
    return found


def sort_expressions(value):
    """The expressions a WFS SORTBY value orders by, as qgswfsgetfeature.cpp reads them."""
    found = []
    for item in groups(value):
        for attribute in item.split(","):
            for suffix in _SORT_SUFFIXES:
                if attribute.endswith(suffix):
                    attribute = attribute[: -len(suffix)]
                    break
            found.append(attribute)
    return found


def looks_like_xml(text):
    """Whether a value or body starts, after white space or a byte order mark, with <, or is UTF-16."""
    if isinstance(text, str):
        return text.lstrip("﻿ \t\r\n").startswith("<")
    return text.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<") or text.startswith((b"\xff\xfe", b"\xfe\xff"))


def xml_parts(text, where="a filter"):
    """(names, functions): the text of every PropertyName and ValueReference, and the name of every Function.

    Raises Refused for a document that is not well-formed, or that has a document type declaration.
    """
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    try:
        root = _parse(data)
    except TransactionError:
        raise Refused("%s is not well-formed XML" % where)
    names, functions = set(), set()
    for element in root.iter():
        local = _local(element.tag)
        if local in ("PropertyName", "ValueReference"):
            names.add("".join(element.itertext()).strip())
        elif local == "Function":
            functions.add(element.get("name") or "")
    names.discard("")
    return names, functions


def wms_filters(value):
    """(OGC filters, SQL conditions) of a WMS FILTER value, read as QgsWmsParameters::layerFilters reads it.

    A part shaped like an OGC filter is read as one, and also as SQL where it
    holds a colon, because QGIS reads it as SQL when the request names fewer
    layers than filters.
    """
    ogc, sql = [], []
    for part in ogc_filter_list(value) or semicolon_list(value):
        if part.startswith("<") and part.endswith("Filter>"):
            ogc.append(part)
        if ":" in part:
            sql.append(part.split(":", 1)[1])
    return ogc, sql


def name_forms(name):
    """A property name as written, and the parts after its last colon and its last slash."""
    return {name, name.rsplit(":", 1)[-1], name.rsplit("/", 1)[-1]}


def named_fields(words, hidden):
    """The hidden fields any of words names, without case, as QGIS looks a field up."""
    folded = {w.casefold() for word in words for w in name_forms(word.strip())}
    return sorted(name for name in hidden if name.casefold() in folded)


def sql_names(condition, hidden):
    """The hidden fields a WMS SQL condition names, matched without case wherever the name stands alone.

    This reads more than SQL would, a quoted value equal to a field's name
    included, which can only refuse more.
    """
    folded = condition.casefold()
    return sorted(
        name for name in hidden if re.search(r"(?<![\w])%s(?![\w])" % re.escape(name.casefold()), folded)
    )


def check_xml(text, hidden, where, check_expression):
    names, functions = xml_parts(text, where)
    not_allowed = sorted(functions - FUNCTIONS)
    if not_allowed:
        raise Refused("%s uses %s, which a filter may not use" % (where, ", ".join(not_allowed)))
    named = named_fields(names, hidden)
    if named:
        raise Refused("%s names %s, which you may not see" % (where, ", ".join(named)))
    # QGIS reads a SortBy property, and may read others, as an expression.
    for name in names:
        check_expression(name, hidden, where, must_parse=False)


def refusal(service, params, body, hidden, check_expression):
    """Why the request's own filters or sorts are refused, or "" when they are not.

    params maps upper-case parameter names to values; hidden holds every field
    the person may not see on any layer of the project; check_expression(text,
    hidden, where, must_parse) raises Refused for an expression that may not run.
    """
    try:
        if service == "WFS":
            for text in semicolon_list(params.get("EXP_FILTER", "")):
                check_expression(text, hidden, "EXP_FILTER", must_parse=True)
            for text in sort_expressions(params.get("SORTBY", "")):
                check_expression(text, hidden, "SORTBY", must_parse=True)
            for text in groups(params.get("FILTER", "")):
                if looks_like_xml(text):
                    check_xml(text, hidden, "FILTER", check_expression)
                elif text.strip():
                    raise Refused("FILTER is not an OGC filter")
        elif service == "WMS":
            if params.get("SLD") or params.get("SLD_BODY"):
                raise Refused("a style sent with the request is not accepted")
            ogc, sql = wms_filters(params.get("FILTER", ""))
            for text in ogc:
                check_xml(text, hidden, "FILTER", check_expression)
            for condition in sql:
                named = sql_names(condition, hidden)
                if named:
                    raise Refused("FILTER names %s, which you may not see" % ", ".join(named))
        if body and looks_like_xml(body):
            check_xml(body, hidden, "the request body", check_expression)
    except Refused as e:
        return str(e)
    return ""


def qgis_check_expression(text, hidden, where, must_parse):
    """Raise Refused for a QGIS expression that names a hidden field, a function outside FUNCTIONS, or a variable.

    With must_parse, text QGIS cannot read is refused; without, it is taken to
    be a plain name, which named_fields has already checked.
    """
    from qgis.core import QgsExpression, QgsFeatureRequest

    expression = QgsExpression(text)
    if expression.hasParserError():
        if must_parse:
            raise Refused("%s is not an expression QGIS can read" % where)
        return
    not_allowed = sorted(set(expression.referencedFunctions()) - FUNCTIONS)
    if not_allowed:
        raise Refused("%s uses %s, which a filter may not use" % (where, ", ".join(not_allowed)))
    if expression.referencedVariables():
        raise Refused("%s uses a variable, which a filter may not use" % where)
    columns = set(expression.referencedColumns())
    if QgsFeatureRequest.ALL_ATTRIBUTES in columns:
        raise Refused("%s reads every field" % where)
    named = named_fields(columns, hidden)
    if named:
        raise Refused("%s names %s, which you may not see" % (where, ", ".join(named)))
