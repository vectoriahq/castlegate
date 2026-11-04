"""Bearer token check with no third-party packages.

RS256 only: the signature is checked with Python's own big-integer pow() and
hashlib, as RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017 section 8.2.2).
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

# DER prefix of the DigestInfo for SHA-256 (RFC 8017 section 9.2, note 1).
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_LEEWAY_SECONDS = 60


class TokenError(Exception):
    pass


def _b64url(data):
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _int(data):
    return int.from_bytes(_b64url(data), "big")


def _verify_rs256(n, e, signing_input, signature):
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_PREFIX + hashlib.sha256(signing_input).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


class Settings:
    def __init__(self):
        self.issuer = os.environ.get("CASTLEGATE_ISSUER", "")
        self.audience = os.environ.get("CASTLEGATE_AUDIENCE", "")
        self.scope = os.environ.get("CASTLEGATE_SCOPE", "")
        self.tenant = os.environ.get("CASTLEGATE_TENANT", "")
        self.email_claim = os.environ.get("CASTLEGATE_EMAIL_CLAIM", "preferred_username")
        self.groups_claim = os.environ.get("CASTLEGATE_GROUPS_CLAIM", "groups")


class KeySet:
    """Signing keys from the issuer's discovery document, cached per process."""

    def __init__(self, settings):
        self.settings = settings
        self.keys = {}
        self.fetched_at = 0

    def _fetch(self):
        base = self.settings.issuer.rstrip("/")
        with urllib.request.urlopen(base + "/.well-known/openid-configuration", timeout=10) as r:
            discovery = json.load(r)
        if discovery.get("issuer") != self.settings.issuer:
            raise TokenError("discovery issuer does not match the configured issuer")
        with urllib.request.urlopen(discovery["jwks_uri"], timeout=10) as r:
            jwks = json.load(r)
        keys = {}
        for jwk in jwks.get("keys", []):
            if jwk.get("kty") == "RSA" and "kid" in jwk:
                keys[jwk["kid"]] = (_int(jwk["n"]), _int(jwk["e"]))
        self.keys = keys
        self.fetched_at = time.time()

    def get(self, kid):
        if kid not in self.keys:
            # One refetch when an unknown key id arrives.
            self._fetch()
        if kid not in self.keys:
            raise TokenError("unknown signing key")
        return self.keys[kid]


class Verifier:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.keyset = KeySet(self.settings)

    def verify(self, authorization):
        """Return the person's identity, or raise TokenError."""
        if not authorization or not authorization.startswith("Bearer "):
            raise TokenError("no bearer token")
        token = authorization[len("Bearer "):].strip()
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenError("malformed token")
        try:
            header = json.loads(_b64url(parts[0]))
            claims = json.loads(_b64url(parts[1]))
            signature = _b64url(parts[2])
        except Exception:
            raise TokenError("malformed token")
        if header.get("alg") != "RS256":
            raise TokenError("algorithm is not RS256")
        n, e = self.keyset.get(header.get("kid"))
        if not _verify_rs256(n, e, (parts[0] + "." + parts[1]).encode("ascii"), signature):
            raise TokenError("bad signature")
        s = self.settings
        now = time.time()
        if claims.get("iss") != s.issuer:
            raise TokenError("wrong issuer")
        aud = claims.get("aud")
        if aud != s.audience and not (isinstance(aud, list) and s.audience in aud):
            raise TokenError("wrong audience")
        if s.tenant and claims.get("tid") != s.tenant:
            raise TokenError("wrong tenant")
        if not isinstance(claims.get("exp"), (int, float)) or now > claims["exp"] + _LEEWAY_SECONDS:
            raise TokenError("expired")
        if "nbf" in claims and now < claims["nbf"] - _LEEWAY_SECONDS:
            raise TokenError("not yet valid")
        if s.scope and s.scope not in str(claims.get("scp", "")).split():
            raise TokenError("scope missing")
        if "_claim_names" in claims:
            raise TokenError("groups overage: the token lists no groups")
        oid = claims.get("oid")
        if not oid:
            raise TokenError("no object id")
        groups = claims.get(s.groups_claim) or []
        return {
            "oid": str(oid),
            "email": str(claims.get(s.email_claim, "")).lower(),
            "groups": [str(g) for g in groups],
        }
