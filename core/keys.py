"""The issuer's signing keys, found through its OpenID Connect discovery document.

Each worker process keeps its own copy. Keys are fetched on first use, again
when they are a day old, and again when a token names a key id the copy lacks,
but never more than once a minute: a stream of tokens naming made-up key ids
would otherwise make every request fetch from the issuer.
"""

import json
import time
import urllib.request

from .rsa import check_public_key
from .tokens import TokenError, b64url_decode

MAX_AGE_SECONDS = 24 * 60 * 60
MIN_REFETCH_SECONDS = 60
MAX_DOCUMENT_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 10


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    # A redirect could lead off HTTPS, so none is followed.
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def fetch_json(url):
    """GET a JSON document over HTTPS, refusing a redirect or a large answer."""
    if not url.startswith("https://"):
        raise TokenError("the issuer's documents must be fetched over HTTPS")
    with _opener.open(url, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(body) > MAX_DOCUMENT_BYTES:
        raise TokenError("the issuer's document is too large")
    return json.loads(body)


class KeySet:
    def __init__(self, settings, fetch=None, now=time.monotonic):
        self.settings = settings
        self._fetch_json = fetch or fetch_json
        self._now = now
        self.keys = {}
        self.fetched_at = None
        self.tried_at = None
        self.error = None

    def _fetch(self):
        self.tried_at = self._now()
        issuer = self.settings.issuer
        try:
            discovery = self._fetch_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
            if not isinstance(discovery, dict) or discovery.get("issuer") != issuer:
                raise TokenError("the discovery document names a different issuer")
            jwks_uri = discovery.get("jwks_uri")
            if not isinstance(jwks_uri, str):
                raise TokenError("the discovery document has no jwks_uri")
            jwks = self._fetch_json(jwks_uri)
            keys = {}
            for jwk in (jwks.get("keys") if isinstance(jwks, dict) else None) or []:
                key = _rsa_key(jwk)
                if key:
                    keys[jwk["kid"]] = key
        except Exception as e:
            # A failed refresh keeps the keys already held.
            self.error = str(e) if isinstance(e, TokenError) else "could not fetch the issuer's signing keys"
            return
        self.keys = keys
        self.fetched_at = self.tried_at
        self.error = None

    def _may_fetch(self):
        return self.tried_at is None or self._now() - self.tried_at >= MIN_REFETCH_SECONDS

    def get(self, kid):
        """The (n, e) for kid, or raise TokenError."""
        stale = self.fetched_at is None or self._now() - self.fetched_at >= MAX_AGE_SECONDS
        if (stale or kid not in self.keys) and self._may_fetch():
            self._fetch()
        if kid not in self.keys:
            raise TokenError(self.error or "the token's signing key is not one of the issuer's")
        return self.keys[kid]


def _rsa_key(jwk):
    """(n, e) for an RSA signing key, or None for a key Castlegate does not use."""
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or not isinstance(jwk.get("kid"), str):
        return None
    if jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
        return None
    try:
        n = int.from_bytes(b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(b64url_decode(jwk["e"]), "big")
        check_public_key(n, e)
    except Exception:
        # Whatever is wrong with one key, the rest of the set is still used.
        return None
    return n, e
