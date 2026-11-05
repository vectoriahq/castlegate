"""The WFS Transaction reader, in plain Python: castlegate_server/transaction.py imports no QGIS."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "server"))

from castlegate_server.transaction import Action, TransactionError, is_transaction, read  # noqa: E402

NS = (
    'xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" '
    'xmlns:gml="http://www.opengis.net/gml" xmlns:qgs="http://www.qgis.org/gml"'
)


def tx(inner, root="wfs:Transaction"):
    return ('<?xml version="1.0"?><%s service="WFS" version="1.0.0" %s>%s</%s>' % (root, NS, inner, root)).encode()


def update(layer, *names, filter_=True):
    props = "".join("<wfs:Property><wfs:Name>%s</wfs:Name><wfs:Value>x</wfs:Value></wfs:Property>" % n for n in names)
    f = '<ogc:Filter><ogc:FeatureId fid="%s.1"/></ogc:Filter>' % layer if filter_ else ""
    return '<wfs:Update typeName="qgs:%s">%s%s</wfs:Update>' % (layer, props, f)


POINT = '<qgs:geometry><gml:Point><gml:coordinates>1,2</gml:coordinates></gml:Point></qgs:geometry>'


class Reading(unittest.TestCase):
    def test_an_update(self):
        self.assertEqual(read(tx(update("assets", "name", "status"))), [Action("update", "assets", frozenset({"name", "status"}), False, 0)])

    def test_an_update_of_the_shape(self):
        self.assertEqual(read(tx(update("assets", "geometry"))), [Action("update", "assets", frozenset(), True, 0)])

    def test_an_insert(self):
        body = tx("<wfs:Insert><qgs:assets>%s<qgs:name>n</qgs:name><qgs:secret>s</qgs:secret><gml:boundedBy/></qgs:assets></wfs:Insert>" % POINT)
        self.assertEqual(read(body), [Action("insert", "assets", frozenset({"name", "secret"}), True, 1)])

    def test_several_features_in_one_insert(self):
        body = tx("<wfs:Insert><qgs:assets><qgs:name>a</qgs:name></qgs:assets><qgs:other><qgs:x>b</qgs:x></qgs:other></wfs:Insert>")
        self.assertEqual([(a.layer, a.fields) for a in read(body)], [("assets", frozenset({"name"})), ("other", frozenset({"x"}))])

    def test_a_delete(self):
        body = tx('<wfs:Delete typeName="qgs:assets"><ogc:Filter><ogc:FeatureId fid="assets.2"/></ogc:Filter></wfs:Delete>')
        self.assertEqual(read(body), [Action("delete", "assets", frozenset(), False, 0)])

    def test_several_actions(self):
        body = tx(update("assets", "name") + '<wfs:Delete typeName="assets"/>')
        self.assertEqual([a.kind for a in read(body)], ["update", "delete"])

    def test_wfs_2_value_reference(self):
        body = tx('<wfs:Update typeName="qgs:assets"><wfs:Property><wfs:ValueReference>secret</wfs:ValueReference></wfs:Property></wfs:Update>')
        self.assertEqual(read(body)[0].fields, frozenset({"secret"}))

    def test_the_type_name_is_the_part_after_the_first_colon_as_qgis_takes_it(self):
        body = tx('<wfs:Update typeName="a:b:c"><wfs:Property><wfs:Name>x</wfs:Name></wfs:Property></wfs:Update>')
        self.assertEqual(read(body)[0].layer, "b")

    def test_type_names_alone_is_not_read_as_the_layer(self):
        # QGIS reads only typeName, so an Update with typeNames alone names no layer.
        body = tx('<wfs:Update typeNames="assets"><wfs:Property><wfs:Name>x</wfs:Name></wfs:Property></wfs:Update>')
        self.assertEqual(read(body)[0].layer, "")


class ReadingWide(unittest.TestCase):
    """Where QGIS's reading could differ, more names are read, never fewer."""

    def test_names_nested_anywhere_in_an_update(self):
        body = tx(
            '<wfs:Update typeName="assets"><wfs:Property><wfs:Name>name</wfs:Name>'
            "<x><wfs:Name>secret</wfs:Name></x></wfs:Property><y><wfs:Property><wfs:Name>status</wfs:Name></wfs:Property></y></wfs:Update>"
        )
        self.assertEqual(read(body)[0].fields, frozenset({"name", "secret", "status"}))

    def test_a_prefixed_name_is_read_both_ways(self):
        self.assertEqual(read(tx(update("assets", "qgs:secret")))[0].fields, frozenset({"qgs:secret", "secret"}))

    def test_whitespace_around_a_name(self):
        action = read(tx(update("assets", " secret ", " geometry ")))[0]
        self.assertEqual((action.fields, action.geometry), (frozenset({"secret"}), True))

    def test_any_namespace_or_none(self):
        body = tx('<Update typeName="assets"><Property><Name>secret</Name></Property></Update>', root="Transaction")
        self.assertEqual(read(body)[0].fields, frozenset({"secret"}))


class Refusing(unittest.TestCase):
    def refused(self, body, message):
        with self.assertRaises(TransactionError) as raised:
            read(body)
        self.assertIn(message, str(raised.exception))

    def test_no_body(self):
        self.refused(b"", "POST body")

    def test_not_xml(self):
        self.refused(b"<wfs:Transaction", "well-formed")
        self.refused(b"not xml", "well-formed")

    def test_another_root(self):
        self.refused(tx("", root="wfs:GetFeature"), "not a WFS Transaction")

    def test_another_action(self):
        self.refused(tx('<wfs:Native vendorId="x">DROP TABLE</wfs:Native>'), "only Insert, Update and Delete")
        self.refused(tx("<wfs:LockId>1</wfs:LockId>"), "only Insert, Update and Delete")

    def test_an_empty_insert(self):
        self.refused(tx("<wfs:Insert></wfs:Insert>"), "no feature")

    def test_a_document_type_declaration(self):
        body = b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY e "secret">]><Transaction><Update typeName="a"><Property><Name>&e;</Name></Property></Update></Transaction>'
        self.refused(body, "document type declaration")

    def test_a_document_type_declaration_in_utf_16(self):
        body = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE t [<!ENTITY e "x">]><Transaction/>'.encode("utf-16")
        self.refused(body, "document type declaration")

    def test_too_large(self):
        from castlegate_server import transaction

        self.refused(b" " * (transaction.MAX_BODY_BYTES + 1), "too large")


class Detecting(unittest.TestCase):
    def test_is_transaction(self):
        self.assertTrue(is_transaction(tx("")))
        self.assertFalse(is_transaction(tx("", root="wfs:GetFeature")))
        self.assertFalse(is_transaction(b"not xml"))


if __name__ == "__main__":
    unittest.main()
