import unittest

from core.rules import NOTHING, Rules, parse_row, parse_rows, substitute
from core.tokens import Person

from .helpers import quote

ALICE = Person(id="aaaa", email="alice@example.org", groups=frozenset({"g1"}))
BOB = Person(id="bbbb", email="bob@example.org", groups=frozenset({"g1", "g2"}))
NO_EMAIL = Person(id="cccc", email="", groups=frozenset({"g1"}))


def row(**values):
    base = {"kind": "person", "who": "alice@example.org", "layer": "assets", "can_see": True}
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
                "kind": " Person ",
                "who": " Alice@Example.org ",
                "layer": "assets",
                "can_see": "yes",
                "can_add": 1,
                "can_change": "TRUE",
                "can_move": False,
                "can_delete": None,
                "see_filter": ' "owner" = {{email}} ',
                "edit_filter": None,
                "hidden_fields": "secret, notes ,",
                "readonly_fields": ["status"],
            }
        )
        self.assertEqual(r.kind, "person")
        self.assertEqual(r.who, "alice@example.org")
        self.assertEqual(r.rights, frozenset({"see", "add", "change"}))
        self.assertEqual(r.see_filter, '"owner" = {{email}}')
        self.assertEqual(r.edit_filter, "")
        self.assertEqual(r.hidden, frozenset({"secret", "notes"}))
        self.assertEqual(r.readonly, frozenset({"status"}))

    def test_rows_that_cannot_be_read_are_left_out_and_reported(self):
        rows, problems = parse_rows(
            [
                row(),
                row(kind="role"),
                row(who=""),
                row(layer=None),
                row(can_see="maybe"),
                row(can_add=2),
                row(kind="group", who="team@example.org"),
                row(see_filter="{{name}} = 1"),
                row(see_filter="{{email"),
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
        self.assertIn("kind is not person or group", problems[0])
        self.assertIn("{{name}}", problems[6])

    def test_a_row_naming_a_person_by_email_object_id_or_group(self):
        by_email = parse_row(row(who="alice@example.org"))
        by_id = parse_row(row(who="AAAA"))
        by_group = parse_row(row(kind="group", who="G1"))
        self.assertTrue(by_email.names(ALICE) and by_id.names(ALICE) and by_group.names(ALICE))
        self.assertFalse(by_email.names(BOB) or by_id.names(BOB))
        self.assertTrue(by_group.names(BOB))

    def test_an_email_row_never_names_a_person_without_an_email(self):
        self.assertFalse(parse_row(row(who="alice@example.org")).names(NO_EMAIL))

    def test_a_person_row_naming_an_id_is_not_a_group(self):
        self.assertFalse(parse_row(row(who="g1")).names(ALICE))


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
        self.assertEqual(answer([row(can_see=False)]), NOTHING)

    def test_rights_add_up(self):
        a = answer([row(can_add=True), row(kind="group", who="g1", can_see=False, can_delete=True)])
        self.assertEqual(a.rights, frozenset({"see", "add", "delete"}))

    def test_see_filters_combine_with_or(self):
        a = answer([row(see_filter='"owner" = {{email}}'), row(kind="group", who="g1", see_filter='"public"')])
        self.assertEqual(a.see_filter, '("owner" = \'alice@example.org\') OR ("public")')

    def test_an_empty_see_filter_means_every_feature_and_wins(self):
        a = answer([row(see_filter='"owner" = {{email}}'), row(kind="group", who="g1")])
        self.assertEqual(a.see_filter, "")

    def test_one_see_filter_is_used_as_it_is(self):
        self.assertEqual(answer([row(see_filter='"public"')]).see_filter, '"public"')

    def test_a_row_that_does_not_let_the_person_see_does_not_widen_what_they_see(self):
        a = answer(
            [
                row(see_filter='"owner" = {{email}}', hidden_fields="secret"),
                row(kind="group", who="g1", can_see=False, can_delete=True),
            ]
        )
        self.assertEqual(a.see_filter, "\"owner\" = 'alice@example.org'")
        self.assertEqual(a.hidden, frozenset({"secret"}))

    def test_no_seeing_row_means_no_features_seen(self):
        self.assertEqual(answer([row(can_see=False, can_add=True)]).see_filter, "FALSE")

    def test_edit_filters_come_from_rows_granting_that_edit(self):
        a = answer(
            [
                row(can_change=True, edit_filter='"owner" = {{email}}'),
                row(kind="group", who="g1", edit_filter=""),
            ]
        )
        self.assertEqual(a.edit_filter("change"), "\"owner\" = 'alice@example.org'")
        for right in ("add", "move", "delete"):
            self.assertEqual(a.edit_filter(right), "FALSE")

    def test_a_row_granting_one_edit_does_not_widen_another(self):
        # Review finding on #1: one filter OR-ed over every editing row let a
        # delete-only row with no filter open changes to every feature.
        a = answer(
            [
                row(can_change=True, edit_filter='"owner" = {{email}}'),
                row(kind="group", who="g1", can_delete=True, edit_filter=""),
            ]
        )
        self.assertEqual(a.edit_filter("change"), "\"owner\" = 'alice@example.org'")
        self.assertEqual(a.edit_filter("move"), "FALSE")
        self.assertEqual(a.edit_filter("delete"), "")
        a = answer([row(can_add=True), row(kind="group", who="g1", can_change=True, edit_filter='"public"')])
        self.assertEqual(a.edit_filter("add"), "")
        self.assertEqual(a.edit_filter("change"), '"public"')

    def test_no_editing_row_means_no_features_edited(self):
        a = answer([row()])
        for right in ("add", "change", "move", "delete"):
            self.assertEqual(a.edit_filter(right), "FALSE")

    def test_a_field_is_hidden_only_where_every_seeing_row_hides_it(self):
        a = answer([row(hidden_fields="secret,notes"), row(kind="group", who="g1", hidden_fields="secret")])
        self.assertEqual(a.hidden, frozenset({"secret"}))
        a = answer([row(hidden_fields="secret"), row(kind="group", who="g1")])
        self.assertEqual(a.hidden, frozenset())

    def test_a_field_is_blocked_only_where_every_row_granting_that_write_blocks_it(self):
        a = answer(
            [
                row(can_change=True, hidden_fields="secret", readonly_fields="status"),
                row(kind="group", who="g1", can_change=True, readonly_fields="status,notes"),
                row(kind="group", who="g1", can_add=True, readonly_fields=""),
                row(kind="group", who="g1", can_see=True, readonly_fields=""),
            ]
        )
        self.assertEqual(a.blocked_on("change"), frozenset({"status"}))
        self.assertEqual(a.blocked_on("add"), frozenset())
        self.assertEqual(a.hidden, frozenset())

    def test_an_add_row_does_not_unblock_a_field_for_changes(self):
        a = answer([row(can_change=True, readonly_fields="status"), row(kind="group", who="g1", can_add=True)])
        self.assertEqual(a.blocked_on("change"), frozenset({"status"}))
        self.assertEqual(a.blocked_on("add"), frozenset())

    def test_no_blocked_list_for_a_write_not_granted(self):
        a = answer([row(can_change=True)])
        self.assertIsNone(a.blocked_on("add"))
        self.assertIsNone(NOTHING.blocked_on("change"))

    def test_hidden_fields_are_blocked_on_writes(self):
        a = answer([row(can_change=True, hidden_fields="secret")])
        self.assertEqual(a.blocked_on("change"), frozenset({"secret"}))

    def test_a_filter_needing_a_missing_email_grants_no_features(self):
        a = answer([row(kind="group", who="g1", see_filter='"owner" = {{email}}')], person=NO_EMAIL)
        self.assertEqual(a.see_filter, "FALSE")

    def test_layer_names_are_matched_exactly(self):
        self.assertEqual(answer([row(layer="Assets")]), NOTHING)


if __name__ == "__main__":
    unittest.main()
