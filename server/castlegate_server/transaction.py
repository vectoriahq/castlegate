"""Reading a WFS Transaction body for what each action touches.

QGIS Server checks a save against the layer's insert, update and delete
permissions and `allowToEdit`, but not against the fields a person may not see
or change. So the plugin reads the body itself, the way QGIS Server's own
reader (`src/server/services/wfs/qgswfstransaction.cpp`) does, and where the
two could differ it reads more names, never fewer: an extra name can only cause
a refusal.

- Actions are the root's direct children named Insert, Update or Delete. Any
  other child is refused rather than ignored.
- An Update or Delete names its layer in the `typeName` attribute; after a
  colon, the part after the first colon, as QGIS takes it.
- An Update's fields are the text of every `Name` or `ValueReference` element
  anywhere inside it, including the text of elements inside those, as
  QDomElement::text() reads it. `geometry` is the feature's shape.
- An Insert's layer is each feature element's local name, and its fields are
  the local names of that element's children, without `boundedBy` and
  `geometry`.

Plain Python, so it is tested without QGIS.
"""

import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from dataclasses import dataclass

MAX_BODY_BYTES = 20 * 1024 * 1024
GEOMETRY = "geometry"


class TransactionError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    kind: str  # "insert", "update" or "delete"
    layer: str  # the WFS type name
    fields: frozenset  # attribute fields it sets; empty for a delete
    geometry: bool  # whether it sets the shape
    features: int  # features an insert adds; 0 for an update or delete, whose count the filter decides


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _type_name(element):
    name = element.get("typeName") or ""
    # QString::section(':', 1, 1): the part after the first colon.
    return name.split(":")[1] if ":" in name else name


def _refuse_doctype(*args):
    raise TransactionError("a transaction may not contain a document type declaration")


def _parse(body):
    """An ElementTree element from the body, built with expat so a document type declaration can be refused."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    # Refusing any DTD refuses entity definitions with it, whatever the body's encoding.
    parser.StartDoctypeDeclHandler = _refuse_doctype
    parser.EntityDeclHandler = _refuse_doctype

    def tag(name):
        uri, _, local = name.rpartition(" ")
        return "{%s}%s" % (uri, local) if uri else local

    parser.StartElementHandler = lambda name, attrs: builder.start(tag(name), {tag(k): v for k, v in attrs.items()})
    parser.EndElementHandler = lambda name: builder.end(tag(name))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(body, True)
        return builder.close()
    except TransactionError:
        raise
    except Exception:  # expat.ExpatError, or the builder's own error on a broken tree
        raise TransactionError("the transaction is not well-formed XML")


def is_transaction(body):
    """
    Whether the body's root element is a WFS Transaction; False for a body that
    is not XML. A body over MAX_BODY_BYTES is not parsed and counts as one, so
    read() refuses it: answering False would pass a large Transaction to QGIS
    Server unread.
    """
    if len(body) > MAX_BODY_BYTES:
        return True
    try:
        return _local(_parse(body).tag) == "Transaction"
    except TransactionError:
        return False


def read(body):
    """The actions of a WFS Transaction body, or raise TransactionError."""
    if not body:
        raise TransactionError("a transaction must be sent as a POST body")
    if len(body) > MAX_BODY_BYTES:
        raise TransactionError("the transaction is too large")
    root = _parse(body)
    if _local(root.tag) != "Transaction":
        raise TransactionError("the body is not a WFS Transaction")
    actions = []
    for element in root:
        kind = _local(element.tag)
        if kind == "Update":
            names = set()
            for child in element.iter():
                if _local(child.tag) in ("Name", "ValueReference"):
                    # QDomElement::text() joins the text of every descendant, so
                    # <Name>sec<x/>ret</Name> names "secret". The text before the
                    # first child is read as well, which can only add a name.
                    names.add("".join(child.itertext()).strip())
                    names.add((child.text or "").strip())
            names.discard("")
            # QGIS matches the text as written; a prefixed form is read as both.
            names |= {n.split(":")[-1] for n in names}
            actions.append(
                Action("update", _type_name(element), frozenset(names - {GEOMETRY}), GEOMETRY in names, 0)
            )
        elif kind == "Delete":
            actions.append(Action("delete", _type_name(element), frozenset(), False, 0))
        elif kind == "Insert":
            features = list(element)
            if not features:
                raise TransactionError("an Insert holds no feature")
            for feature in features:
                names = {_local(child.tag) for child in feature} - {"boundedBy"}
                actions.append(
                    Action("insert", _local(feature.tag), frozenset(names - {GEOMETRY}), GEOMETRY in names, 1)
                )
        else:
            raise TransactionError("a transaction may hold only Insert, Update and Delete, not %s" % kind)
    return actions
