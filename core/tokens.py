"""From an Authorization header to a person and their groups.

Every check the design names is made here: the RS256 signature by key id, the
issuer, the audience, the tenant when one is set, the expiry and not-before
times, the scope, and the groups overage marker. Anything that fails raises
TokenError, whose message says why and is safe to show the person.
"""

import base64
import json
import re
import time
from dataclasses import dataclass

from .rsa import InvalidKey, verify_rs256

LEEWAY_SECONDS = 60
MAX_TOKEN_BYTES = 16 * 1024

# \Z, not $: $ also matches before a trailing newline.
_B64URL = re.compile(r"[A-Za-z0-9_-]*\Z")


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class Person:
    # The provider's immutable id for the person: Entra's object ID by default.
    id: str
    # Lower case. Empty when the token carries no email claim.
    email: str
    # Group object IDs, lower case.
    groups: frozenset


def b64url_decode(text):
    """Strict base64url without padding, as JSON Web Tokens use. Raises only TokenError."""
    if not isinstance(text, str) or not _B64URL.match(text) or len(text) % 4 == 1:
        raise TokenError("the token is malformed")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise TokenError("the token is malformed")


def _no_duplicates(pairs):
    # Two values for one claim could be read differently by two readers.
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise TokenError("the token repeats a claim")
        obj[key] = value
    return obj


def _no_constants(name):
    raise TokenError("the token is malformed")


def _json(part):
    try:
        value = json.loads(b64url_decode(part), object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except TokenError:
        raise
    except Exception:
        raise TokenError("the token is malformed")
    if not isinstance(value, dict):
        raise TokenError("the token is malformed")
    return value


def _number(value):
    # abs(value) < inf is False for infinity and NaN, which a literal such as 1e999 parses to.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < float("inf")


def verify(authorization, settings, keyset, now=time.time):
    """The Person an Authorization header signs in as, or raise TokenError."""
    if settings.missing:
        raise TokenError("the server's sign-in settings are incomplete: " + ", ".join(settings.missing))
    if not authorization:
        raise TokenError("no bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise TokenError("no bearer token")
    token = token.strip()
    if len(token) > MAX_TOKEN_BYTES:
        raise TokenError("the token is too large")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("the token is malformed")
    header, claims = _json(parts[0]), _json(parts[1])
    signature = b64url_decode(parts[2])

    if header.get("alg") != "RS256":
        raise TokenError("the token is not signed with RS256")
    if "crit" in header:
        raise TokenError("the token has critical header parameters")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise TokenError("the token names no signing key")
    n, e = keyset.get(kid)
    try:
        valid = verify_rs256(n, e, (parts[0] + "." + parts[1]).encode("ascii"), signature)
    except InvalidKey:
        raise TokenError("the issuer's signing key is not accepted")
    if not valid:
        raise TokenError("the token's signature is not valid")

    if claims.get("iss") != settings.issuer:
        raise TokenError("the token is from a different issuer")
    aud = claims.get("aud")
    if not (aud == settings.audience or (isinstance(aud, list) and settings.audience in aud)):
        raise TokenError("the token is for a different app")
    if settings.tenant and claims.get("tid") != settings.tenant:
        raise TokenError("the token is from a different tenant")
    t = now()
    exp = claims.get("exp")
    if not _number(exp) or t > exp + LEEWAY_SECONDS:
        raise TokenError("the token has expired")
    nbf = claims.get("nbf")
    if nbf is not None and (not _number(nbf) or t < nbf - LEEWAY_SECONDS):
        raise TokenError("the token is not valid yet")
    scp = claims.get("scp")
    if not isinstance(scp, str) or settings.scope not in scp.split():
        raise TokenError("the token does not carry the scope " + settings.scope)
    if "_claim_names" in claims or "hasgroups" in claims:
        raise TokenError("the person is in too many groups for the token to list them")

    person_id = claims.get(settings.id_claim)
    if not isinstance(person_id, str) or not person_id:
        raise TokenError("the token has no %s claim" % settings.id_claim)
    email = claims.get(settings.email_claim, "")
    if not isinstance(email, str):
        raise TokenError("the token's %s claim is not text" % settings.email_claim)
    groups = claims.get(settings.groups_claim, [])
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise TokenError("the token's %s claim is not a list of ids" % settings.groups_claim)
    return Person(id=person_id.lower(), email=email.strip().lower(), groups=frozenset(g.lower() for g in groups))
