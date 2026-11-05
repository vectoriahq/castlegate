"""RS256 signature check with Python's own big-integer pow() and hashlib.

RSASSA-PKCS1-v1_5 verification with SHA-256, RFC 8017 section 8.2.2. The check
builds the one encoded message a valid signature can produce and compares it
whole, rather than parsing the decrypted block, so no padding or DigestInfo
parsing is involved.
"""

import hashlib
import hmac

# DER prefix of the DigestInfo for SHA-256 (RFC 8017 section 9.2, note 1).
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

MIN_MODULUS_BITS = 2048


class InvalidKey(ValueError):
    """A public key that is refused before any signature is checked."""


def check_public_key(n, e):
    """Raise InvalidKey unless (n, e) is an RSA public key Castlegate accepts."""
    if not isinstance(n, int) or not isinstance(e, int):
        raise InvalidKey("key values are not integers")
    if n.bit_length() < MIN_MODULUS_BITS:
        raise InvalidKey("key is shorter than %d bits" % MIN_MODULUS_BITS)
    if e < 3 or e % 2 == 0 or e >= n:
        raise InvalidKey("key exponent is not valid")


def verify_rs256(n, e, signing_input, signature):
    """True when signature is a valid RS256 signature of signing_input under (n, e)."""
    check_public_key(n, e)
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_PREFIX + hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)
