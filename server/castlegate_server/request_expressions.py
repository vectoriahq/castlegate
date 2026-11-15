"""The filters and sorts a request carries, checked before QGIS Server runs them.

QGIS Server runs a request's own filter or sort without asking the access
filter about it. So a filter on a hidden field tells the person its values by
which features come back, and a filter calling a function such as
`aggregate()` or `get_feature()` reads other layers, the rules table
included. The plugin refuses a request whose filter or sort names a field
hidden from the person on a layer they may see, by its name or its alias,
uses a function outside `FUNCTIONS`, or uses a variable.

What is read, following QGIS Server's own readers:

- WFS `EXP_FILTER`: QGIS expressions, split at every `;`
  (QgsServerParameterDefinition::toExpressionList).
- WFS `SORTBY`: a list split like QgsWfsParameter::toStringListWithExp, each
  item split at `,` with its order suffix taken off: the expression QGIS
  orders by (qgswfsgetfeature.cpp).
- WFS `FILTER` and WMS `FILTER`: OGC filters. A WMS `layer:condition` item
  is refused: QGIS hands its condition to the data provider as SQL
  (QgsWmsParameters::layerFilters, setSubsetString), which the plugin cannot
  read as the database will.
- A body QGIS may read as XML (may_be_xml).
- WMS `SLD` and `SLD_BODY`: refused whatever they hold, because a style sent
  with the request can label features with any field.

An OGC filter or XML body is read for every `PropertyName` and
`ValueReference` in it, wherever it stands, and every `Function`. That reads
more than QGIS acts on, which can only refuse more.

The condition the module holds to: every filter, sort and body QGIS Server
may read is read here as QGIS will read it, and one that cannot be is refused.

The splitting and the XML reading are plain Python, tested without QGIS;
checking an expression uses QGIS's own parser.
"""

import re
import xml.parsers.expat as expat

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


_BYTE_ORDER_MARKS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")


def may_be_xml(text):
    """Whether QGIS may read a value or body as XML.

    A value: it starts with <, after white space or a byte order mark. A body:
    the same, or it starts with a byte order mark, or it holds a zero byte. Qt
    works out a body's encoding from its first bytes, so UTF-16 or UTF-32
    without a byte order mark is XML to it; neither can be form data.
    """
    if isinstance(text, str):
        return text.lstrip("\ufeff \t\r\n").startswith("<")
    data = bytes(text)
    return (
        data.startswith(_BYTE_ORDER_MARKS)
        or b"\x00" in data
        or data.lstrip(b" \t\r\n").startswith(b"<")
    )


_NAME_ELEMENTS = ("PropertyName", "ValueReference")


class _OpenName:
    """The pieces of one PropertyName or ValueReference read so far."""

    def __init__(self):
        self.pieces = set()
        self.run = []
        self.text = []

    def boundary(self):
        if self.run:
            self.pieces.add("".join(self.run))
            self.run = []


def _refuse_doctype(*args):
    raise Refused("a document type declaration is not accepted")


def xml_parts(text, where="a filter"):
    """(names, functions): every name a PropertyName or ValueReference could give, and every Function's name.

    QGIS takes a property's name from its first child node, whatever kind it is
    (QgsOgcUtils, nodeColumnRefFromOgcFilter: element.firstChild().nodeValue()),
    where QDom keeps text, CDATA sections, comments, processing instructions and
    elements as separate nodes. So each of those pieces is a name here, and so
    is the element's whole text, which can only refuse more.

    Raises Refused for a document that is not well-formed, or that has a document type declaration.
    """
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse_doctype
    parser.EntityDeclHandler = _refuse_doctype
    names, functions, open_names = set(), set(), []

    def boundary():
        for name in open_names:
            name.boundary()

    def start(tag, attrs):
        boundary()
        local = tag.rpartition(" ")[2]
        if local == "Function":
            functions.add(attrs.get("name") or "")
        if local in _NAME_ELEMENTS:
            open_names.append(_OpenName())
        stack.append(local)

    def end(tag):
        boundary()
        if stack.pop() in _NAME_ELEMENTS:
            name = open_names.pop()
            names.update(name.pieces)
            names.add("".join(name.text))

    def characters(chunk):
        for name in open_names:
            name.run.append(chunk)
            name.text.append(chunk)

    def other_node(*values):
        boundary()
        for name in open_names:
            name.pieces.update(v for v in values if isinstance(v, str))

    stack = []
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.CommentHandler = other_node
    parser.ProcessingInstructionHandler = other_node
    parser.StartCdataSectionHandler = boundary
    parser.EndCdataSectionHandler = boundary
    try:
        parser.Parse(data, True)
    except Refused:
        raise Refused("%s has a document type declaration, which is not accepted" % where)
    except Exception:  # expat.ExpatError
        raise Refused("%s is not well-formed XML" % where)
    return {n.strip() for n in names} - {""}, functions


def wms_filters(value):
    """(OGC filters, other parts) of a WMS FILTER value, split as QgsWmsParameters::layerFilters splits it.

    QGIS reads an OGC-shaped part as SQL when the request names fewer layers
    than filters. Such a part's last token ends in Filter>, which QGIS's own
    token check (QgsRenderer::testFilterStringSafety) refuses, so it is never
    run as SQL.
    """
    ogc, other = [], []
    for part in ogc_filter_list(value) or semicolon_list(value):
        if part.startswith("<") and part.endswith("Filter>"):
            ogc.append(part)
        elif part:
            other.append(part)
    return ogc, other


def name_forms(name):
    """A property name as written, and the parts after its last colon and its last slash."""
    return {name, name.rsplit(":", 1)[-1], name.rsplit("/", 1)[-1]}


def named_fields(words, hidden):
    """The hidden fields any of words names, without case, as QGIS looks a field up."""
    folded = {w.casefold() for word in words for w in name_forms(word.strip())}
    return sorted(name for name in hidden if name.casefold() in folded)


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
    hidden from the person on a layer they may see, and each such field's alias
    (with_aliases); check_expression(text, hidden, where, must_parse) raises
    Refused for an expression that may not run.
    """
    try:
        if service == "WFS":
            for text in semicolon_list(params.get("EXP_FILTER", "")):
                check_expression(text, hidden, "EXP_FILTER", must_parse=True)
            for text in sort_expressions(params.get("SORTBY", "")):
                check_expression(text, hidden, "SORTBY", must_parse=True)
            for text in groups(params.get("FILTER", "")):
                if may_be_xml(text):
                    check_xml(text, hidden, "FILTER", check_expression)
                elif text.strip():
                    raise Refused("FILTER is not an OGC filter")
        elif service == "WMS":
            if params.get("SLD") or params.get("SLD_BODY"):
                raise Refused("a style sent with the request is not accepted")
            ogc, other = wms_filters(params.get("FILTER", ""))
            if other:
                raise Refused("FILTER may hold only OGC filters, not a condition")
            for text in ogc:
                check_xml(text, hidden, "FILTER", check_expression)
        if body and may_be_xml(body):
            check_xml(body, hidden, "the request body", check_expression)
    except Refused as e:
        return str(e)
    return ""


def with_aliases(hidden, project):
    """hidden, with the alias of every field of the project's vector layers whose name it holds.

    QGIS finds a field named in an expression or an OGC filter by its name, then
    by its name without case, then by its alias without case
    (QgsFields::lookupField), so a hidden field could be named by its alias.
    """
    from qgis.core import QgsVectorLayer

    folded = {name.casefold() for name in hidden}
    found = set(hidden)
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            for f in layer.fields():
                if f.name().casefold() in folded and f.alias():
                    found.add(f.alias())
    return frozenset(found)


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
