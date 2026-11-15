import unittest

from core.rules import NOTHING, Rules, parse_row, parse_rows, substitute
from core.tokens import Person

from .helpers import quote

ALICE = Person(id="aaaa", email="alice@example.org", groups=frozenset({"g1"}))
BOB = Person(id="bbbb", email="bob@example.org", groups=frozenset({"g1", "g2"}))
NO_EMAIL = Person(id="cccc", email="", groups=frozenset({"g1"}))


def row(**values):
    base = {"who_type": "user", "who": "alice@example.org", "layer": "assets", "can_read": True}
    base.update(values)
    return base


def answer(records, person=ALICE, layer="assets"):
    rows, problems = parse_rows(records)
    assert not problems, problems
    return Rules(rows).answer(person, layer, quote)


class Reading(unittest.TestCase):
    def test_a_full_row(self):
        r = parse_row(
            {
                "who_type": " User ",
                "who": " Alice@Example.org ",
                "layer": "assets",
                "can_read": "yes",
                "can_insert": 1,
                "can_update_attributes": "TRUE",
                "can_update_geometry": False,
                "can_delete": None,
                "read_filter": ' "owner" = {{email}} ',
                "edit_filter": None,
                "hidden_fields": "secret, notes ,",
                "readonly_fields": ["status"],
            }
        )
        self.assertEqual(r.who_type, "user")
        self.assertEqual(r.who, "alice@example.org")
        self.assertEqual(r.rights, frozenset({"read", "insert", "update_attributes"}))
        self.assertEqual(r.read_filter, '"owner" = {{email}}')
        self.assertEqual(r.edit_filter, "")
        self.assertEqual(r.hidden, frozenset({"secret", "notes"}))
        self.assertEqual(r.readonly, frozenset({"status"}))

    def test_rows_that_cannot_be_read_are_left_out_and_reported(self):
        rows, problems = parse_rows(
            [
                row(),
                row(who_type="role"),
                row(who=""),
                row(layer=None),
                row(can_read="maybe"),
                row(can_insert=2),
                row(who_type="group", who="team@example.org"),
                row(read_filter="{{name}} = 1"),
                row(read_filter="{{email"),
                row(hidden_fields=7),
                row(hidden_fields=["a", 1]),
                row(who=5),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            [p.split(":")[0] for p in problems],
            ["row %d" % n for n in range(2, 13)],
        )
        self.assertIn("who_type is not user or group", problems[0])
        self.assertIn("{{name}}", problems[6])

    def test_a_row_naming_a_person_by_email_object_id_or_group(self):
        by_email = parse_row(row(who="alice@example.org"))
        by_id = parse_row(row(who="aaaa"))
        by_group = parse_row(row(who_type="group", who="g1"))
        self.assertTrue(by_email.names(ALICE) and by_id.names(ALICE) and by_group.names(ALICE))
        self.assertFalse(by_email.names(BOB) or by_id.names(BOB))
        self.assertTrue(by_group.names(BOB))

    def test_a_guid_matches_in_any_case(self):
        oid, group = "aaaaaaaa-0000-4000-8000-00000000000a", "11111111-0000-4000-8000-000000000001"
        person = Person(id=oid, email="", groups=frozenset({group}))
        self.assertTrue(parse_row(row(who=oid.upper())).names(person))
        self.assertTrue(parse_row(row(who_type="group", who=group.upper())).names(person))

    def test_an_id_that_is_not_a_guid_matches_exactly(self):
        # Review finding: an OpenID Connect sub is case-sensitive, so lower-casing
        # every id made two people named AbC and aBc one person.
        person = Person(id="AbC", email="", groups=frozenset({"Editors"}))
        self.assertTrue(parse_row(row(who="AbC")).names(person))
        self.assertFalse(parse_row(row(who="abc")).names(person))
        self.assertTrue(parse_row(row(who_type="group", who="Editors")).names(person))
        self.assertFalse(parse_row(row(who_type="group", who="editors")).names(person))

    def test_only_a_to_z_are_lower_cased_in_an_email(self):
        # Review finding: str.lower() turns the Kelvin sign into k, so a row for
        # kim@example.org also named the holder of "\u212aim@example.org".
        kim = Person(id="dddddddd-0000-4000-8000-00000000000d", email="kim@example.org", groups=frozenset())
        self.assertTrue(parse_row(row(who="KIM@Example.org")).names(kim))
        self.assertFalse(parse_row(row(who="\u212aim@example.org")).names(kim))

    def test_an_email_row_never_names_a_person_without_an_email(self):
        self.assertFalse(parse_row(row(who="alice@example.org")).names(NO_EMAIL))

    def test_a_person_row_naming_an_id_is_not_a_group(self):
        self.assertFalse(parse_row(row(who="g1")).names(ALICE))


class HiddenAnywhere(unittest.TestCase):
    def test_every_field_hidden_on_a_layer_the_person_may_see(self):
        rows, problems = parse_rows([
            row(layer="assets", hidden_fields="secret"),
            row(layer="notes", hidden_fields="author, cost"),
            row(layer="notes", can_read=False, can_insert=True, hidden_fields="other"),
            row(layer="unseen", can_read=False, can_insert=True, hidden_fields="never"),
            row(who="bob@example.org", layer="assets", hidden_fields="bobs"),
        ])
        self.assertEqual(problems, [])
        self.assertEqual(Rules(rows).hidden_anywhere(ALICE, quote), frozenset({"secret", "author", "cost"}))
        self.assertEqual(Rules(rows).hidden_anywhere(NO_EMAIL, quote), frozenset())


class Substituting(unittest.TestCase):
    def test_values_are_quoted(self):
        self.assertEqual(substitute('"owner" = {{ email }} OR "id" = {{oid}}', ALICE, quote), "\"owner\" = 'alice@example.org' OR \"id\" = 'aaaa'")

    def test_a_quote_in_a_value_stays_inside_the_literal(self):
        person = Person(id="x", email="o'brien@example.org", groups=frozenset())
        self.assertEqual(substitute('"owner" = {{email}}', person, quote), "\"owner\" = 'o''brien@example.org'")

    def test_a_placeholder_inside_a_value_is_not_substituted_again(self):
        person = Person(id="x' OR TRUE OR '", email="a{{oid}}@example.org", groups=frozenset())
        self.assertEqual(substitute('"owner" = {{email}}', person, quote), "\"owner\" = 'a{{oid}}@example.org'")

    def test_a_missing_value_makes_the_expression_false(self):
        self.assertEqual(substitute('"owner" = {{email}}', NO_EMAIL, quote), "FALSE")
        self.assertEqual(substitute('"id" = {{oid}}', NO_EMAIL, quote), "\"id\" = 'cccc'")


class Combining(unittest.TestCase):
    def test_no_rows_means_nothing(self):
        self.assertEqual(answer([]), NOTHING)
        self.assertEqual(answer([row(who="bob@example.org")]), NOTHING)
        self.assertEqual(answer([row()], layer="other"), NOTHING)
        self.assertTrue(NOTHING.nothing)

    def test_a_row_granting_nothing_means_nothing(self):
        self.assertEqual(answer([row(can_read=False)]), NOTHING)

    def test_rights_add_up(self):
        a = answer([row(can_insert=True), row(who_type="group", who="g1", can_read=False, can_delete=True)])
        self.assertEqual(a.rights, frozenset({"read", "insert", "delete"}))

    def test_read_filters_combine_with_or(self):
        a = answer([row(read_filter='"owner" = {{email}}'), row(who_type="group", who="g1", read_filter='"public"')])
        self.assertEqual(a.read_filter, '("owner" = \'alice@example.org\') OR ("public")')

    def test_an_empty_read_filter_means_every_feature_and_wins(self):
        a = answer([row(read_filter='"owner" = {{email}}'), row(who_type="group", who="g1")])
        self.assertEqual(a.read_filter, "")

    def test_one_read_filter_is_used_as_it_is(self):
        self.assertEqual(answer([row(read_filter='"public"')]).read_filter, '"public"')

    def test_a_row_that_does_not_let_the_person_see_does_not_widen_what_they_see(self):
        a = answer(
            [
                row(read_filter='"owner" = {{email}}', hidden_fields="secret"),
                row(who_type="group", who="g1", can_read=False, can_delete=True),
            ]
        )
        self.assertEqual(a.read_filter, "\"owner\" = 'alice@example.org'")
        self.assertEqual(a.hidden, frozenset({"secret"}))

    def test_no_seeing_row_means_no_features_seen(self):
        self.assertEqual(answer([row(can_read=False, can_insert=True)]).read_filter, "FALSE")

    def test_edit_filters_come_from_rows_granting_that_edit(self):
        a = answer(
            [
                row(can_update_attributes=True, edit_filter='"owner" = {{email}}'),
                row(who_type="group", who="g1", edit_filter=""),
            ]
        )
        self.assertEqual(a.edit_filter("update_attributes"), "\"owner\" = 'alice@example.org'")
        for right in ("insert", "update_geometry", "delete"):
            self.assertEqual(a.edit_filter(right), "FALSE")

    def test_a_row_granting_one_edit_does_not_widen_another(self):
        # Review finding on #1: one filter OR-ed over every editing row let a
        # delete-only row with no filter open changes to every feature.
        a = answer(
            [
                row(can_update_attributes=True, edit_filter='"owner" = {{email}}'),
                row(who_type="group", who="g1", can_delete=True, edit_filter=""),
            ]
        )
        self.assertEqual(a.edit_filter("update_attributes"), "\"owner\" = 'alice@example.org'")
        self.assertEqual(a.edit_filter("update_geometry"), "FALSE")
        self.assertEqual(a.edit_filter("delete"), "")
        a = answer([row(can_insert=True), row(who_type="group", who="g1", can_update_attributes=True, edit_filter='"public"')])
        self.assertEqual(a.edit_filter("insert"), "")
        self.assertEqual(a.edit_filter("update_attributes"), '"public"')

    def test_no_editing_row_means_no_features_edited(self):
        a = answer([row()])
        for right in ("insert", "update_attributes", "update_geometry", "delete"):
            self.assertEqual(a.edit_filter(right), "FALSE")

    def test_a_field_is_hidden_only_where_every_seeing_row_hides_it(self):
        a = answer([row(hidden_fields="secret,notes"), row(who_type="group", who="g1", hidden_fields="secret")])
        self.assertEqual(a.hidden, frozenset({"secret"}))
        a = answer([row(hidden_fields="secret"), row(who_type="group", who="g1")])
        self.assertEqual(a.hidden, frozenset())

    def test_a_field_is_blocked_only_where_every_row_granting_that_write_blocks_it(self):
        a = answer(
            [
                row(can_update_attributes=True, hidden_fields="secret", readonly_fields="status"),
                row(who_type="group", who="g1", can_update_attributes=True, readonly_fields="status,notes"),
                row(who_type="group", who="g1", can_insert=True, readonly_fields=""),
                row(who_type="group", who="g1", can_read=True, readonly_fields=""),
            ]
        )
        self.assertEqual(a.blocked_on("update_attributes"), frozenset({"status"}))
        self.assertEqual(a.blocked_on("insert"), frozenset())
        self.assertEqual(a.hidden, frozenset())

    def test_an_add_row_does_not_unblock_a_field_for_changes(self):
        a = answer([row(can_update_attributes=True, readonly_fields="status"), row(who_type="group", who="g1", can_insert=True)])
        self.assertEqual(a.blocked_on("update_attributes"), frozenset({"status"}))
        self.assertEqual(a.blocked_on("insert"), frozenset())

    def test_no_blocked_list_for_a_write_not_granted(self):
        a = answer([row(can_update_attributes=True)])
        self.assertIsNone(a.blocked_on("insert"))
        self.assertIsNone(NOTHING.blocked_on("update_attributes"))

    def test_hidden_fields_are_blocked_on_writes(self):
        a = answer([row(can_update_attributes=True, hidden_fields="secret")])
        self.assertEqual(a.blocked_on("update_attributes"), frozenset({"secret"}))

    def test_a_filter_needing_a_missing_email_grants_no_features(self):
        a = answer([row(who_type="group", who="g1", read_filter='"owner" = {{email}}')], person=NO_EMAIL)
        self.assertEqual(a.read_filter, "FALSE")

    def test_layer_names_are_matched_exactly(self):
        self.assertEqual(answer([row(layer="Assets")]), NOTHING)


if __name__ == "__main__":
    unittest.main()
