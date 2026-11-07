"""The request's own filters and sorts, read without QGIS.

QGIS's expression parser is replaced by a stand-in that records what it is
asked to check, so these tests cover what is read and from where; the checks
against a real QGIS Server are in tests/server/run.mjs.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "server"))

from castlegate_server import request_expressions as rx  # noqa: E402

HIDDEN = frozenset({"secret"})
OGC = 'xmlns:ogc="http://www.opengis.net/ogc"'


def ogc(inner):
    return "<ogc:Filter %s>%s</ogc:Filter>" % (OGC, inner)


def equal(name, value="x"):
    return "<ogc:PropertyIsEqualTo><ogc:PropertyName>%s</ogc:PropertyName><ogc:Literal>%s</ogc:Literal></ogc:PropertyIsEqualTo>" % (name, value)


class Expressions:
    """Stands in for QGIS: refuses text naming "secret" or calling "aggregate(", and records every check."""

    def __init__(self):
        self.seen = []

    def __call__(self, text, hidden, where, must_parse):
        self.seen.append((where, text, must_parse))
        if "secret" in text.lower() and "secret" in hidden:
            raise rx.Refused("%s names secret" % where)
        if "aggregate(" in text:
            raise rx.Refused("%s uses aggregate" % where)


def refusal(service, params, body=b"", hidden=HIDDEN):
    return rx.refusal(service, params, body, hidden, Expressions())


class Splitting(unittest.TestCase):
    def test_groups_as_qgis_reads_a_list(self):
        self.assertEqual(rx.groups("(a)(b c)"), ["a", "b c"])
        self.assertEqual(rx.groups("a,b"), ["a,b"])
        self.assertEqual(rx.groups(""), [])

    def test_sortby_items_lose_their_order_suffix(self):
        self.assertEqual(rx.sort_expressions("name ASC,status+D,secret DESC,x"), ["name", "status", "secret", "x"])
        self.assertEqual(rx.sort_expressions("(name A)(secret D)"), ["name", "secret"])

    def test_wms_ogc_filters_as_qgis_splits_them(self):
        self.assertEqual(rx.ogc_filter_list("(<Filter>a</Filter>)()(<Filter>b</Filter>)"), ["<Filter>a</Filter>", "", "<Filter>b</Filter>"])
        self.assertEqual(rx.ogc_filter_list("<Filter>a</Filter>"), ["<Filter>a</Filter>"])
        self.assertEqual(rx.ogc_filter_list('assets:"a" = 1'), [])

    def test_wms_sql_conditions(self):
        self.assertEqual(rx.wms_filters('assets:"a" = 1;notes,more:"b" = 2'), ([], ['"a" = 1', '"b" = 2']))

    def test_a_wms_ogc_filter_with_a_colon_is_read_as_sql_too(self):
        ogc_part = ogc(equal("name"))
        found_ogc, found_sql = rx.wms_filters("(%s)" % ogc_part)
        self.assertEqual(found_ogc, [ogc_part])
        self.assertEqual(len(found_sql), 1)


class Names(unittest.TestCase):
    def test_a_field_is_matched_without_case_and_without_a_prefix_or_path(self):
        for name in ("secret", "SECRET", "qgs:secret", "qgs:assets/qgs:secret", " secret "):
            self.assertEqual(rx.named_fields({name}, HIDDEN), ["secret"], name)
        self.assertEqual(rx.named_fields({"secrets", "name"}, HIDDEN), [])

    def test_sql_names_a_field_wherever_it_stands_alone(self):
        self.assertEqual(rx.sql_names('"Secret" = 1', HIDDEN), ["secret"])
        self.assertEqual(rx.sql_names("secret IS NULL", HIDDEN), ["secret"])
        self.assertEqual(rx.sql_names('"secrets" = 1 AND "my_secret" = 2', HIDDEN), [])


class Refusals(unittest.TestCase):
    def test_exp_filter_parts_are_each_checked_and_must_parse(self):
        checks = Expressions()
        self.assertEqual(rx.refusal("WFS", {"EXP_FILTER": '"name" = 1;"status" = 2'}, b"", HIDDEN, checks), "")
        self.assertEqual(checks.seen, [("EXP_FILTER", '"name" = 1', True), ("EXP_FILTER", '"status" = 2', True)])
        self.assertIn("secret", refusal("WFS", {"EXP_FILTER": '"name" = 1;"secret" = 2'}))
        self.assertIn("aggregate", refusal("WFS", {"EXP_FILTER": "aggregate('r', 'count', $id) > 0"}))

    def test_sortby_is_checked(self):
        self.assertIn("SORTBY", refusal("WFS", {"SORTBY": "name ASC,secret DESC"}))
        self.assertEqual(refusal("WFS", {"SORTBY": "name ASC"}), "")

    def test_a_wfs_filter_naming_a_hidden_field(self):
        self.assertIn("secret", refusal("WFS", {"FILTER": ogc(equal("secret"))}))
        self.assertIn("secret", refusal("WFS", {"FILTER": "(%s)(%s)" % (ogc(equal("name")), ogc(equal("qgs:Secret")))}))
        self.assertEqual(refusal("WFS", {"FILTER": ogc(equal("name"))}), "")

    def test_a_property_split_by_an_element_or_written_as_an_entity_is_read_whole(self):
        self.assertIn("secret", refusal("WFS", {"FILTER": ogc(equal("sec<x/>ret"))}))
        self.assertIn("secret", refusal("WFS", {"FILTER": ogc(equal("&#115;ecret"))}))

    def test_a_function_outside_the_list_is_refused(self):
        call = '<ogc:PropertyIsGreaterThan><ogc:Function name="aggregate"><ogc:Literal>r</ogc:Literal></ogc:Function><ogc:Literal>0</ogc:Literal></ogc:PropertyIsGreaterThan>'
        self.assertIn("aggregate", refusal("WFS", {"FILTER": ogc(call)}, hidden=frozenset()))
        self.assertEqual(refusal("WFS", {"FILTER": ogc(call.replace("aggregate", "lower"))}, hidden=frozenset()), "")

    def test_a_filter_that_is_not_xml_or_has_a_dtd_is_refused(self):
        self.assertIn("not an OGC filter", refusal("WFS", {"FILTER": '"secret" = 1'}))
        self.assertIn("not well-formed", refusal("WFS", {"FILTER": "<ogc:Filter>"}))
        self.assertIn("not well-formed", refusal("WFS", {"FILTER": '<!DOCTYPE f [<!ENTITY e "secret">]><Filter><PropertyName>&e;</PropertyName></Filter>'}))

    def test_a_bbox_and_a_feature_id_filter_pass(self):
        bbox = '<ogc:BBOX><gml:Envelope xmlns:gml="http://www.opengis.net/gml"><gml:lowerCorner>0 0</gml:lowerCorner><gml:upperCorner>1 1</gml:upperCorner></gml:Envelope></ogc:BBOX>'
        self.assertEqual(refusal("WFS", {"FILTER": ogc(bbox)}), "")
        self.assertEqual(refusal("WFS", {"FILTER": ogc('<ogc:FeatureId fid="assets.2"/>')}), "")

    def test_wms_filters(self):
        self.assertIn("secret", refusal("WMS", {"FILTER": 'assets:"secret" = \'x\''}))
        self.assertIn("secret", refusal("WMS", {"FILTER": "(%s)" % ogc(equal("secret"))}))
        self.assertEqual(refusal("WMS", {"FILTER": 'assets:"name" = \'x\''}), "")

    def test_a_style_sent_with_a_wms_request_is_refused(self):
        self.assertIn("style", refusal("WMS", {"SLD_BODY": "<StyledLayerDescriptor/>"}, hidden=frozenset()))
        self.assertIn("style", refusal("WMS", {"SLD": "https://example.org/style.sld"}, hidden=frozenset()))

    def test_an_xml_body_is_read_for_every_property_and_function(self):
        get_feature = '<wfs:GetFeature xmlns:wfs="http://www.opengis.net/wfs"><wfs:Query typeName="assets">%s</wfs:Query></wfs:GetFeature>'
        self.assertIn("secret", refusal("WFS", {}, (get_feature % ogc(equal("secret"))).encode()))
        sort = '<ogc:SortBy %s><ogc:SortProperty><ogc:PropertyName>secret</ogc:PropertyName></ogc:SortProperty></ogc:SortBy>' % OGC
        self.assertIn("secret", refusal("WFS", {}, (get_feature % sort).encode()))
        self.assertEqual(refusal("WFS", {}, (get_feature % ogc(equal("name"))).encode()), "")

    def test_a_body_that_is_not_xml_is_left_to_qgis(self):
        self.assertEqual(refusal("WFS", {}, b"SERVICE=WFS&REQUEST=GetFeature"), "")

    def test_a_body_that_looks_like_xml_but_is_not_is_refused(self):
        self.assertIn("not well-formed", refusal("WFS", {}, b"<wfs:GetFeature"))

    def test_with_nothing_hidden_a_plain_filter_passes(self):
        self.assertEqual(refusal("WFS", {"FILTER": ogc(equal("secret"))}, hidden=frozenset()), "")


if __name__ == "__main__":
    unittest.main()
