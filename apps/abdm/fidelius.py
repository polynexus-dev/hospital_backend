"""
ABDM health-information encryption ("Fidelius", NHA's reference scheme for
the HIE-CM data flow, M2 and M3).

  1. Each side makes a key pair on Curve25519 and a random 32-byte nonce, and
     sends the public key and nonce in `keyMaterial`.
  2. shared secret = ECDH(own private key, other's public key): the
     x-coordinate, 32 bytes big-endian.
  3. xor = own nonce XOR other's nonce; salt = xor[:20], iv = xor[-12:].
  4. AES key = HKDF-SHA256(shared secret, salt, no info), 32 bytes.
  5. content = base64(AES-256-GCM(key, iv, data)): ciphertext then 16-byte tag.

The curve is the *short Weierstrass* form of Curve25519 as Bouncy Castle
implements it ("Curve25519" in its custom curves, which Fidelius uses), and
public keys travel as X.509 SubjectPublicKeyInfo with explicit curve
parameters (the "MIIBMTCB6gYHKoZIzj0CATCB3gIBATAr..." strings in ABDM's
samples). Python's `cryptography` only offers the Montgomery form (X25519),
so this module maps between the two — exactly, since the forms are
birationally equivalent:

    Weierstrass x = Montgomery u + A/3 (mod p),  y = v

Our private scalars are generated already clamped, so an X25519 ladder with
them is plain scalar multiplication — the same result Bouncy Castle's ECDH
gets on the Weierstrass curve. Every curve constant below is *derived* from
p and A, not copied; tests cross-check the derivation against the published
Wei25519 constants and an independent affine-arithmetic implementation.

STATUS: interoperability with a real HIP/HIU has not been exercised — that
needs sandbox credentials. The first sandbox data transfer, or NHA's Fidelius
test vectors, is the check; everything here is unit-tested for internal
consistency and against the curve maths.
"""
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

P = 2**255 - 19
A = 486662  # Montgomery: v^2 = u^3 + A u^2 + u
_THIRD = pow(3, -1, P)
_DELTA = A * _THIRD % P  # x = u + A/3

# Short Weierstrass y^2 = x^3 + a x + b, from the Montgomery curve (B = 1).
CURVE_A = (3 - A * A) * _THIRD % P
CURVE_B = (2 * A**3 - 9 * A) * pow(27, -1, P) % P
# Base point: u = 9 and RFC 7748's v.
_BASE_V = 14781619447589544791020593568409986887264606134616475288964881837755586237401
GX, GY = (9 + _DELTA) % P, _BASE_V
ORDER = 2**252 + 27742317777372353535851937790883648493
COFACTOR = 8

CRYPTO_ALG = "ECDH"
CURVE_NAME = "Curve25519"
KEY_PARAMETERS = "Curve25519/32byte random key"


class FideliusError(ValueError):
    pass


# --- DER (just enough for an EC SubjectPublicKeyInfo) ----------------------

def _der_len(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag, body):
    return bytes([tag]) + _der_len(len(body)) + body


def _int(n):
    body = n.to_bytes((n.bit_length() + 8) // 8 or 1, "big")  # a leading 0 keeps it positive
    return _tlv(0x02, body)


_OID_EC_PUBLIC_KEY = bytes.fromhex("06072a8648ce3d0201")  # 1.2.840.10045.2.1
_OID_PRIME_FIELD = bytes.fromhex("06072a8648ce3d0101")  # 1.2.840.10045.1.1


def _point(x, y):
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def _ec_parameters():
    return _tlv(0x30, b"".join([
        _int(1),
        _tlv(0x30, _OID_PRIME_FIELD + _int(P)),
        _tlv(0x30, _tlv(0x04, CURVE_A.to_bytes(32, "big")) + _tlv(0x04, CURVE_B.to_bytes(32, "big"))),
        _tlv(0x04, _point(GX, GY)),
        _int(ORDER),
        _int(COFACTOR),
    ]))


def _read_tlv(data, pos):
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    return tag, data[pos:pos + length], pos + length


def _point_from_spki(der):
    """The uncompressed point inside an X.509 SubjectPublicKeyInfo."""
    tag, body, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise FideliusError("Public key is not an X.509 SubjectPublicKeyInfo.")
    _, _, pos = _read_tlv(body, 0)  # AlgorithmIdentifier (parameters are fixed: Curve25519)
    tag, bits, _ = _read_tlv(body, pos)
    if tag != 0x03 or bits[:1] != b"\x00":
        raise FideliusError("Public key has no point.")
    return bits[1:]


# --- Points ---------------------------------------------------------------

def _sqrt(n):
    """Square root mod p (p = 5 mod 8)."""
    r = pow(n, (P + 3) // 8, P)
    if r * r % P != n % P:
        r = r * pow(2, (P - 1) // 4, P) % P
    if r * r % P != n % P:
        raise FideliusError("Not a point on Curve25519.")
    return r


def _y_for(x):
    return _sqrt((x**3 + CURVE_A * x + CURVE_B) % P)


def _decode_public_key(key_value):
    raw = base64.b64decode(key_value)
    point = raw if len(raw) == 65 else _point_from_spki(raw)
    if len(point) != 65 or point[0] != 4:
        raise FideliusError("Expected an uncompressed Curve25519 point.")
    x, y = int.from_bytes(point[1:33], "big"), int.from_bytes(point[33:], "big")
    if (y * y - (x**3 + CURVE_A * x + CURVE_B)) % P:
        raise FideliusError("Public key is not on Curve25519.")
    return x


# --- Keys -----------------------------------------------------------------

@dataclass
class KeyPair:
    private_key: str  # base64 of the clamped 32-byte little-endian scalar
    public_key: str  # base64 X.509 SubjectPublicKeyInfo
    nonce: str  # base64, 32 random bytes


def generate_key_pair() -> KeyPair:
    k = bytearray(secrets.token_bytes(32))
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64  # clamped: X25519 then multiplies by exactly this scalar
    u = int.from_bytes(X25519PrivateKey.from_private_bytes(bytes(k)).public_key().public_bytes_raw(), "little")
    x = (u + _DELTA) % P
    spki = _tlv(0x30, _tlv(0x30, _OID_EC_PUBLIC_KEY + _ec_parameters()) + _tlv(0x03, b"\x00" + _point(x, _y_for(x))))
    return KeyPair(
        private_key=base64.b64encode(bytes(k)).decode(),
        public_key=base64.b64encode(spki).decode(),
        nonce=base64.b64encode(secrets.token_bytes(32)).decode(),
    )


def shared_secret(private_key: str, their_public_key: str) -> bytes:
    x_theirs = _decode_public_key(their_public_key)
    u_theirs = (x_theirs - _DELTA) % P
    k = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key))
    u_shared = int.from_bytes(k.exchange(X25519PublicKey.from_public_bytes(u_theirs.to_bytes(32, "little"))), "little")
    return ((u_shared + _DELTA) % P).to_bytes(32, "big")


def _session(private_key, own_nonce, their_public_key, their_nonce):
    a, b = base64.b64decode(own_nonce), base64.b64decode(their_nonce)
    if len(a) != 32 or len(b) != 32:
        raise FideliusError("Nonces must be 32 bytes.")
    xor = bytes(i ^ j for i, j in zip(a, b))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=xor[:20], info=None).derive(shared_secret(private_key, their_public_key))
    return AESGCM(key), xor[-12:]


def encrypt(data: str, *, private_key, own_nonce, their_public_key, their_nonce) -> str:
    aes, iv = _session(private_key, own_nonce, their_public_key, their_nonce)
    return base64.b64encode(aes.encrypt(iv, data.encode("utf-8"), None)).decode()


def decrypt(content: str, *, private_key, own_nonce, their_public_key, their_nonce) -> str:
    aes, iv = _session(private_key, own_nonce, their_public_key, their_nonce)
    try:
        return aes.decrypt(iv, base64.b64decode(content), None).decode("utf-8")
    except Exception as exc:  # InvalidTag, bad base64, bad UTF-8: all "not for us"
        raise FideliusError("Could not decrypt the health information.") from exc


def key_material(pair: KeyPair, *, valid_for=timedelta(hours=1)) -> dict:
    """The `keyMaterial` object ABDM data-flow messages carry."""
    expiry = (datetime.now(timezone.utc) + valid_for).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "cryptoAlg": CRYPTO_ALG,
        "curve": CURVE_NAME,
        "dhPublicKey": {"expiry": expiry, "parameters": KEY_PARAMETERS, "keyValue": pair.public_key},
        "nonce": pair.nonce,
    }


def checksum(data: str) -> str:
    """MD5 of the plain FHIR bundle, as the data-push `entries[].checksum`
    (an integrity check the HIU repeats after decrypting, not a security
    control — the GCM tag is that)."""
    return hashlib.md5(data.encode("utf-8"), usedforsecurity=False).hexdigest()
