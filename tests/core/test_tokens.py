import json
import time
import unittest
from dataclasses import replace

from core import rsa
from core.settings import Settings
from core.tokens import Person, TokenError, verify

from .helpers import SETTINGS, StaticKeys, b64url, claims, key, token


def replaced(name, literal):
    """The claims as JSON, with one claim's value written as the given literal."""
    c = claims()
    return json.dumps(c).replace('"%s": %d' % (name, c[name]), '"%s": %s' % (name, literal))


def check(tok, settings=SETTINGS, keys=None, now=time.time):
    return verify("Bearer " + tok, settings, keys or StaticKeys(key()), now=now)


class Accepted(unittest.TestCase):
    def test_a_valid_token_names_the_person_and_groups(self):
        person = check(token())
        self.assertEqual(
            person,
            Person(
                id="00000000-0000-0000-0000-0000000000aa",
                email="alice@example.org",
                groups=frozenset({"11111111-2222-3333-4444-555555555555"}),
            ),
        )

    def test_the_audience_may_be_one_of_a_list(self):
        check(token(claims(aud=["other", SETTINGS.audience])))

    def test_the_scope_may_be_one_of_several(self):
        check(token(claims(scp="read " + SETTINGS.scope + " write")))

    def test_no_groups_claim_means_no_groups(self):
        self.assertEqual(check(token(claims(groups=None))).groups, frozenset())

    def test_no_email_claim_means_an_empty_email(self):
        self.assertEqual(check(token(claims(preferred_username=None))).email, "")

    def test_the_claims_read_follow_the_settings(self):
        settings = replace(SETTINGS, id_claim="sub", email_claim="email", groups_claim="roles_groups")
        person = check(token(claims(sub="S-1", email="B@Example.org", roles_groups=["G"])), settings)
        self.assertEqual(person, Person(id="S-1", email="b@example.org", groups=frozenset({"G"})))

    def test_a_guid_is_lower_cased_and_other_ids_kept_as_written(self):
        person = check(token(claims(oid="00000000-0000-0000-0000-0000000000AA", groups=["AbC"])))
        self.assertEqual((person.id, person.groups), ("00000000-0000-0000-0000-0000000000aa", frozenset({"AbC"})))

    def test_only_a_to_z_are_lower_cased_in_an_email(self):
        # Review finding: str.lower() turned the Kelvin sign into k, so this signed in as kim@example.org.
        email = check(token(claims(preferred_username="\u212aIM@example.org"))).email
        self.assertEqual(email, "\u212aim@example.org")

    def test_a_token_within_the_leeway_of_its_times(self):
        now = time.time()
        check(token(claims(exp=int(now) - 30, nbf=int(now) + 30)))

    def test_the_bearer_scheme_is_case_insensitive(self):
        verify("bearer " + token(), SETTINGS, StaticKeys(key()))


class Refused(unittest.TestCase):
    def refused(self, tok, message, **kwargs):
        with self.assertRaises(TokenError) as raised:
            check(tok, **kwargs)
        self.assertIn(message, str(raised.exception))

    def test_no_header(self):
        for header in (None, "", "Basic abc", "Bearer ", "Bearer"):
            with self.assertRaises(TokenError):
                verify(header, SETTINGS, StaticKeys(key()))

    def test_incomplete_settings_refuse_every_token(self):
        settings = Settings.from_environment({"CASTLEGATE_ISSUER": SETTINGS.issuer})
        self.refused(token(), "CASTLEGATE_AUDIENCE, CASTLEGATE_SCOPE", settings=settings)

    def test_a_bad_signature(self):
        h, p, s = token().split(".")
        other = token(claims(oid="someone-else")).split(".")[1]
        self.refused(h + "." + other + "." + s, "signature is not valid")

    def test_a_token_signed_by_another_key_with_the_same_kid(self):
        impostor = key("impostor", kid=key().kid)
        self.refused(token(signer=impostor), "signature is not valid")

    def test_alg_none_and_other_algorithms(self):
        for alg in ("none", "HS256", "RS512", "PS256", None):
            header = {"alg": alg, "kid": key().kid}
            self.refused(token(header=header), "not signed with RS256")

    def test_hs256_with_the_public_key_as_secret_is_refused_before_any_check(self):
        tok = token(header={"alg": "HS256", "kid": key().kid})
        self.refused(tok, "RS256")

    def test_an_empty_signature(self):
        h, p, _ = token().split(".")
        self.refused(h + "." + p + ".", "signature is not valid")

    def test_critical_header_parameters(self):
        self.refused(token(header={"alg": "RS256", "kid": key().kid, "crit": ["exp"]}), "critical")

    def test_no_kid(self):
        self.refused(token(header={"alg": "RS256"}), "names no signing key")

    def test_an_unknown_kid(self):
        self.refused(token(header={"alg": "RS256", "kid": "unknown"}), "not one of the issuer's")

    def test_the_wrong_issuer(self):
        self.refused(token(claims(iss="https://login.example.test/other/v2.0")), "different issuer")

    def test_the_wrong_audience(self):
        self.refused(token(claims(aud="api://other")), "different app")
        self.refused(token(claims(aud=["api://other"])), "different app")
        self.refused(token(claims(aud=None)), "different app")

    def test_the_wrong_tenant(self):
        self.refused(token(claims(tid="tenant-2")), "different tenant")

    def test_expired(self):
        self.refused(token(claims(exp=int(time.time()) - 120)), "expired")

    def test_no_expiry(self):
        self.refused(token(claims(exp=None)), "expired")
        self.refused(token(claims(exp="9999999999")), "expired")
        self.refused(token(claims(exp=True)), "expired")

    def test_not_yet_valid(self):
        self.refused(token(claims(nbf=int(time.time()) + 120)), "not valid yet")
        self.refused(token(claims(nbf="0")), "not valid yet")

    def test_infinity_and_nan_are_not_numbers(self):
        payload = replaced("exp", "Infinity")
        self.assertIn("Infinity", payload)
        self.refused(token(raw_payload=payload.encode()), "malformed")

    def test_a_number_too_large_for_a_float_is_not_a_time(self):
        # Review finding on #1: 1e999 parses to infinity, so the token never expired.
        for literal in ("1e999", "-1e999"):
            payload = replaced("exp", literal)
            self.assertIn(literal, payload)
            self.refused(token(raw_payload=payload.encode()), "expired")
        payload = replaced("nbf", "-1e999")
        self.refused(token(raw_payload=payload.encode()), "not valid yet")

    def test_scope_missing(self):
        self.refused(token(claims(scp="other")), "scope")
        self.refused(token(claims(scp=None)), "scope")
        self.refused(token(claims(scp=["access_as_user"])), "scope")
        self.refused(token(claims(scp="access_as_user_extra")), "scope")

    def test_groups_overage(self):
        overage = claims(groups=None, _claim_names={"groups": "src1"}, _claim_sources={"src1": {}})
        self.refused(token(overage), "too many groups")
        self.refused(token(claims(hasgroups=True)), "too many groups")

    def test_no_object_id(self):
        self.refused(token(claims(oid=None)), "no oid claim")
        self.refused(token(claims(oid=7)), "no oid claim")

    def test_claims_of_the_wrong_type(self):
        self.refused(token(claims(preferred_username=["a@b"])), "not text")
        self.refused(token(claims(groups="g1")), "not a list")
        self.refused(token(claims(groups=[1, 2])), "not a list")

    def test_a_repeated_claim(self):
        payload = json.dumps(claims())[:-1] + ', "oid": "someone-else"}'
        self.refused(token(raw_payload=payload.encode()), "repeats a claim")

    def test_malformed_tokens(self):
        h, p, s = token().split(".")
        for bad in ("abc", h + "." + p, h + "." + p + "." + s + ".x", h + "." + p + "." + s + "=", "!!." + p + "." + s):
            self.refused(bad, "malformed")
        self.refused(b64url(b"[1]") + "." + p + "." + s, "malformed")
        self.refused(h + "." + b64url(b"not json") + "." + s, "malformed")

    def test_base64url_is_strict(self):
        from core.tokens import b64url_decode

        for bad in ("abc\n", "AQABAQ\n", "ab=c", "ab+c", "ab/c", "abcde", "ab c", 7, None):
            with self.assertRaises(TokenError):
                b64url_decode(bad)
        self.assertEqual(b64url_decode("AQAB"), b"\x01\x00\x01")
        # Review finding: "AQ" and "AR" both decoded to b"\x01", so one value had
        # several spellings. Only the encoder's own spelling is accepted.
        self.assertEqual(b64url_decode("AQ"), b"\x01")
        for other in ("AR", "AS", "Af"):
            with self.assertRaises(TokenError):
                b64url_decode(other)

    def test_a_signature_respelt_is_refused(self):
        h, p, s = token().split(".")
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        # A 2048-bit signature is 256 bytes, so its last character carries 2 bits
        # and 4 unused ones: the characters in its group of 16 decode alike.
        group = alphabet.index(s[-1]) >> 4
        other = next(c for c in alphabet if c != s[-1] and alphabet.index(c) >> 4 == group)
        self.refused(h + "." + p + "." + s[:-1] + other, "malformed")

    def test_a_huge_token(self):
        self.refused(token(claims(pad="x" * 20000)), "too large")


class Keys(unittest.TestCase):
    def test_a_short_key_is_refused(self):
        short = key("short", bits=1024)
        with self.assertRaises(TokenError) as raised:
            check(token(signer=short), keys=StaticKeys(short))
        self.assertIn("not accepted", str(raised.exception))

    def test_an_even_exponent_is_refused(self):
        k = key()
        with self.assertRaises(rsa.InvalidKey):
            rsa.check_public_key(k.n, 4)

    def test_a_signature_as_large_as_the_modulus_is_refused(self):
        k = key()
        size = (k.n.bit_length() + 7) // 8
        self.assertFalse(rsa.verify_rs256(k.n, k.e, b"x", k.n.to_bytes(size, "big")))


if __name__ == "__main__":
    unittest.main()
