"""Test keys, tokens and a stand-in for QGIS's quoting function, in plain Python.

Keys are generated on each run, so no private key is kept in the repository.
"""

import base64
import hashlib
import json
import secrets
import time

from core.settings import Settings

ISSUER = "https://login.example.test/tenant-1/v2.0"
AUDIENCE = "api://castlegate-test"
SCOPE = "access_as_user"
TENANT = "tenant-1"
SETTINGS = Settings(issuer=ISSUER, audience=AUDIENCE, scope=SCOPE, tenant=TENANT)

_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _probable_prime(n, rounds=40):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _probable_prime(candidate):
            return candidate


class Key:
    def __init__(self, bits=2048, e=65537, kid=None):
        while True:
            p, q = _prime(bits // 2), _prime(bits // 2)
            phi = (p - 1) * (q - 1)
            if p != q and phi % e:
                break
        self.n, self.e, self.d = p * q, e, pow(e, -1, phi)
        self.kid = kid or secrets.token_hex(8)

    def sign(self, signing_input):
        k = (self.n.bit_length() + 7) // 8
        t = _SHA256_PREFIX + hashlib.sha256(signing_input).digest()
        em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(k, "big")

    def jwk(self, **extra):
        def b64(i):
            return b64url(i.to_bytes((i.bit_length() + 7) // 8, "big"))

        return dict({"kty": "RSA", "kid": self.kid, "use": "sig", "n": b64(self.n), "e": b64(self.e)}, **extra)


_keys = {}


def key(name="main", **kwargs):
    """A key generated once per test run and name."""
    if name not in _keys:
        _keys[name] = Key(**kwargs)
    return _keys[name]


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def claims(**overrides):
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "tid": TENANT,
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "scp": SCOPE,
        "oid": "00000000-0000-0000-0000-0000000000aa",
        "preferred_username": "Alice@Example.org",
        "groups": ["11111111-2222-3333-4444-555555555555"],
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def token(payload=None, signer=None, header=None, raw_payload=None):
    signer = signer or key()
    head = {"alg": "RS256", "typ": "JWT", "kid": signer.kid}
    if header is not None:
        head = header
    h = b64url(json.dumps(head).encode())
    p = b64url(raw_payload if raw_payload is not None else json.dumps(payload or claims()).encode())
    s = b64url(signer.sign((h + "." + p).encode("ascii")))
    return h + "." + p + "." + s


class StaticKeys:
    """A key set holding the given keys, with no fetching."""

    def __init__(self, *keys):
        self.keys = {k.kid: (k.n, k.e) for k in keys}

    def get(self, kid):
        from core.tokens import TokenError

        if kid not in self.keys:
            raise TokenError("the token's signing key is not one of the issuer's")
        return self.keys[kid]


def quote(text):
    """Python's copy of QgsExpression::quotedString on qgis/QGIS master, 2026-11-05."""
    text = text.replace("'", "''").replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
    return "'%s'" % text
