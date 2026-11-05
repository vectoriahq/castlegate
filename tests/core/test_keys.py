import unittest

from core import keys as keys_module
from core.keys import KeySet, fetch_json
from core.tokens import TokenError

from .helpers import ISSUER, SETTINGS, key

JWKS_URI = "https://login.example.test/tenant-1/discovery/v2.0/keys"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Issuer:
    """Answers the discovery document and the key set, counting fetches."""

    def __init__(self, *jwks):
        self.jwks = list(jwks)
        self.discovery = {"issuer": ISSUER, "jwks_uri": JWKS_URI}
        self.fetches = 0
        self.fail = False

    def __call__(self, url):
        if self.fail:
            raise OSError("network down")
        if url == ISSUER + "/.well-known/openid-configuration":
            return self.discovery
        if url == JWKS_URI:
            self.fetches += 1
            return {"keys": self.jwks}
        raise AssertionError("unexpected fetch " + url)


class KeySetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.issuer = Issuer(key().jwk())
        self.keys = KeySet(SETTINGS, fetch=self.issuer, now=self.clock)

    def test_a_known_key_is_fetched_once(self):
        k = key()
        self.assertEqual(self.keys.get(k.kid), (k.n, k.e))
        self.assertEqual(self.keys.get(k.kid), (k.n, k.e))
        self.assertEqual(self.issuer.fetches, 1)

    def test_an_unknown_kid_refetches_at_most_once_a_minute(self):
        self.keys.get(key().kid)
        for _ in range(5):
            with self.assertRaises(TokenError):
                self.keys.get("made-up")
        self.assertEqual(self.issuer.fetches, 1)
        self.clock.t += keys_module.MIN_REFETCH_SECONDS
        with self.assertRaises(TokenError):
            self.keys.get("made-up")
        self.assertEqual(self.issuer.fetches, 2)

    def test_a_rotated_key_is_found_by_refetching(self):
        self.keys.get(key().kid)
        new = key("rotated")
        self.issuer.jwks = [new.jwk()]
        self.clock.t += keys_module.MIN_REFETCH_SECONDS
        self.assertEqual(self.keys.get(new.kid), (new.n, new.e))

    def test_keys_are_refreshed_after_a_day_and_a_withdrawn_key_stops_working(self):
        old = key()
        self.keys.get(old.kid)
        self.issuer.jwks = [key("rotated").jwk()]
        self.clock.t += keys_module.MAX_AGE_SECONDS
        with self.assertRaises(TokenError):
            self.keys.get(old.kid)

    def test_a_failed_refresh_keeps_the_keys_held(self):
        k = key()
        self.keys.get(k.kid)
        self.issuer.fail = True
        self.clock.t += keys_module.MAX_AGE_SECONDS
        self.assertEqual(self.keys.get(k.kid), (k.n, k.e))
        with self.assertRaises(TokenError) as raised:
            self.keys.get("made-up")
        self.assertIn("could not fetch", str(raised.exception))

    def test_no_keys_when_the_issuer_cannot_be_reached(self):
        self.issuer.fail = True
        with self.assertRaises(TokenError):
            self.keys.get(key().kid)

    def test_a_discovery_document_naming_another_issuer_is_refused(self):
        self.issuer.discovery = {"issuer": "https://evil.example.test", "jwks_uri": JWKS_URI}
        with self.assertRaises(TokenError) as raised:
            self.keys.get(key().kid)
        self.assertIn("different issuer", str(raised.exception))

    def test_keys_castlegate_does_not_use_are_left_out(self):
        k = key()
        short = key("short", bits=1024)
        self.issuer.jwks = [
            dict(k.jwk(), kid="enc", use="enc"),
            dict(k.jwk(), kid="ps", alg="PS256"),
            dict(k.jwk(), kid="ec", kty="EC"),
            dict(k.jwk(), kid="bad", n="!!"),
            # Review finding on #1: a trailing newline passed the pattern and
            # raised a non-TokenError, losing every key in the set.
            dict(k.jwk(), kid="newline-n", n=k.jwk()["n"] + "\n"),
            dict(k.jwk(), kid="newline-e", e="AQAB\n"),
            dict(k.jwk(), kid="number", n=12345),
            dict(short.jwk(), kid="short"),
            {"kty": "RSA", "kid": "partial"},
            "not a key",
            k.jwk(),
        ]
        self.assertEqual(self.keys.get(k.kid), (k.n, k.e))
        self.assertEqual(set(self.keys.keys), {k.kid})


class FetchTests(unittest.TestCase):
    def test_only_https_is_fetched(self):
        for url in ("http://login.example.test/x", "file:///etc/passwd", "ftp://x"):
            with self.assertRaises(TokenError):
                fetch_json(url)


if __name__ == "__main__":
    unittest.main()
