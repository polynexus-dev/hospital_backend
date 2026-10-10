# API Encryption Guide (for the dev team)

Audience: the **React frontend team**, the **Flutter mobile team**, and QA doing manual testing in Postman. Both client teams implement the *same* protocol (§3) against the *same* endpoint — the React section (§5) and Flutter section (§5b) below are two implementations of one spec, so if something's unclear in one, check the other.

This doc explains the encryption layers the API uses, when they kick in, and exactly what you need to do (often nothing) to call the API correctly.

---

## 1. The three layers, at a glance

| Layer | What | Where | Always on? |
|---|---|---|---|
| **1 — Transport** | HTTPS/TLS | Nginx/load balancer in front of Django | Yes, in every real environment |
| **2 — Payload encryption** | ECDH-P256 handshake → AES-256-GCM encrypt/decrypt of request & response **bodies** | `apps.core.middleware.PayloadEncryptionMiddleware` | Only when `PAYLOAD_ENCRYPTION_ENABLED=True` (prod). Off by default in dev. |
| **3 — Field-level encryption** | AES-256-GCM (legacy rows: Fernet) encryption of specific DB columns (Aadhaar/national ID, insurance policy number, phone, free-text clinical notes) | `apps.core.encryption` / `apps.core.fields` | Always, for the specific model fields that use `EncryptedCharField`/`EncryptedTextField`/`EncryptedJSONField` |

**The important thing for day-to-day API work: Layer 3 is completely invisible to you.** It's encrypted in the database and decrypted back to plain values by the model field before it ever reaches a serializer or view. You never encode/decode it from client code — just read/write those fields as normal JSON strings.

**Layer 2 is the one that changes how you call the API**, and it's the focus of this doc.

---

## 2. Do I even need to do anything?

- **Local dev (`DEBUG=True`, default `.env`)**: `PAYLOAD_ENCRYPTION_ENABLED` defaults to `False`. The middleware is a transparent no-op — send/receive plain JSON exactly as you always have. Postman, curl, and the existing frontend all work with zero changes.
- **Staging/production**: `PAYLOAD_ENCRYPTION_ENABLED=True`. Every request/response body (with a short bypass list, see §4) is wrapped in `{"enc": "gcm2$..."}`. You must do the ECDH handshake first.
- **The React frontend already implements this for you** (`Frontend/src/api/payloadCrypto.ts` + `Frontend/src/api/client.ts`). If you're building a *new* client (mobile app, a script, a second frontend), read on.

If you just want to call the API manually (Postman/curl) against a server with encryption enabled, see §6.

---

## 3. How Layer 2 works (the handshake)

1. Client generates an **ephemeral P-256 key pair** (Web Crypto API in the browser, or any ECDH-capable crypto lib elsewhere). Never reused across sessions.
2. Client `POST`s its public key to `/api/v1/session-key/`:
   ```json
   { "client_public_key": "<urlsafe-base64, uncompressed X9.62 point>" }
   ```
3. Server performs ECDH with its own (process-lifetime) key pair, expands the shared secret to a 32-byte key via **HKDF-SHA256** (`info="hospital-crm-payload-v1"`, no salt), and caches that AES-256 key server-side under a random `session_id` (TTL = refresh-token lifetime, 12h by default).
4. Server responds:
   ```json
   { "session_id": "<uuid>", "server_public_key": "<urlsafe-base64 point>" }
   ```
5. Client independently derives the **same** AES-256 key from its own private key + the server's public key (same ECDH + HKDF params). The AES key itself is never transmitted.
6. Client stores `session_id` and the derived `CryptoKey` in memory for the life of the tab/session (lost on refresh — that's fine, redo the handshake).

From here on, every request the client sends must carry the header:

```
X-Session-Id: <session_id>
```

### Request encryption

Instead of sending your normal JSON body, send:

```json
{ "enc": "gcm2$<urlsafe-base64(12-byte-nonce || AES-256-GCM ciphertext)>" }
```

where the ciphertext is `AES-256-GCM(key, nonce, JSON.stringify(yourPayload))`, no AAD.

### Response encryption

Any JSON response (status ≠ 204, `Content-Type: application/json`) comes back the same way: `{"enc": "gcm2$..."}`. Decrypt with the same AES key to get the real JSON body.

### Token format

```
gcm2$<urlsafe base64 of (12-byte nonce + ciphertext+tag)>
```
The `gcm2$` prefix is deliberate — it's distinct from the `gcm1$` prefix used by Layer-3 field encryption, so the two are never confused if you ever see raw DB values.

---

## 4. Endpoints that are NEVER encrypted (bypass list)

These paths pass through as plain JSON even when `PAYLOAD_ENCRYPTION_ENABLED=True`, because they're the handshake itself or auth bootstrap (no session/user context yet):

```
/api/v1/session-key/
/api/v1/auth/login/
/api/v1/auth/refresh/
/api/v1/auth/logout/
/api/schema/
/api/schema/swagger-ui/
/admin/
```

Call these exactly as documented elsewhere (plain JSON in, plain JSON out) — don't wrap/unwrap them.

Also note: if a request simply **doesn't** carry `X-Session-Id` (e.g. you haven't done the handshake, or you're a third-party integration that doesn't know about this layer), the middleware passes it through unencrypted rather than failing. This exists so health checks / Postman / third-party webhooks don't break. **It does mean Layer 2 is opt-in per-request, not a hard server-side requirement** — don't rely on it as your only security control; Layer 1 (TLS) is what's mandatory.

If you *do* send `X-Session-Id` but the session has expired or was never created, you get:

```json
HTTP 401
{ "detail": "Encryption session not found or expired. Re-initialise via POST /api/v1/session-key/." }
```
→ just redo the handshake and retry.

---

## 5. Reference implementation (frontend)

Don't reinvent this — copy the pattern from the existing frontend if you're writing a new client:

- [`Frontend/src/api/payloadCrypto.ts`](../Frontend/src/api/payloadCrypto.ts) — `initSession()`, `encryptPayload()`, `decryptPayload()`, using the browser's native Web Crypto API (no extra dependency).
- [`Frontend/src/api/client.ts`](../Frontend/src/api/client.ts) — wires encryption into every `api.get/post/patch/put/delete` call transparently: attaches `X-Session-Id`, encrypts outgoing bodies, decrypts `{"enc": ...}` responses. View code/serializers never see any of this.
- [`Frontend/src/main.tsx`](../Frontend/src/main.tsx) — calls `initSession()` once at app boot, before anything renders. If the server has encryption disabled, `/session-key/` 404s and it's caught silently — the app just runs in plain-JSON mode.

**Key implementation details to match exactly if you port this to another platform (mobile, another JS app, etc.):**
- Curve: **P-256** (secp256r1), uncompressed point encoding, urlsafe-base64 (no padding) for all key/ciphertext transport.
- KDF: **HKDF-SHA256**, `salt = empty`, `info = "hospital-crm-payload-v1"` (ASCII), output length 32 bytes.
- Cipher: **AES-256-GCM**, 12-byte random nonce per message, no AAD, nonce prepended to ciphertext before base64.
- Get the info string and curve wrong and you'll get AES-GCM auth-tag failures (400 `"Encrypted payload could not be decrypted."`), not a clear "key mismatch" error — double-check those two constants first if decryption fails.

---

## 5b. Flutter / Dart implementation notes

**Read this before writing any code — the obvious library choice doesn't work on native mobile.**

### The gotcha: `package:cryptography`'s P-256 ECDH is browser-only

[`package:cryptography`](https://pub.dev/packages/cryptography) (the most commonly recommended Dart crypto package) ships an `EcdhP256` API, and it's tempting to assume it "just works" since the package is cross-platform. **It doesn't, for this specific algorithm.** NIST curves (`EcdhP256`/`EcdhP384`/`EcdhP521`) in that package are implemented **only via the browser's Web Cryptography API** — on Flutter web that's fine, but on the Dart VM (i.e. a real Android/iOS app, which is what "Flutter mobile" means) calling `EcdhP256` throws `UnimplementedError`. The `cryptography_flutter` plugin, which bridges some algorithms to native platform crypto for speed, also does **not** add ECDH support on Android or iOS (it adds `FlutterEcdh` on **macOS only**, via Apple's CryptoKit).

So: plan for **two different libraries**, split by what each is good at:

| Need | Library | Why |
|---|---|---|
| ECDH key generation + agreement on **P-256**, point encode/decode | [`pointycastle`](https://pub.dev/packages/pointycastle) (`ECDHBasicAgreement`, `ECCurve_secp256r1` / `ECDomainParameters('secp256r1')`) | Pure Dart, works on Android/iOS/web/desktop — no native-platform dependency |
| HKDF-SHA256, AES-256-GCM | `package:cryptography` (`Hkdf(hmac: Hmac.sha256())`, `AesGcm.with256bits()`) | Pure Dart implementations of both exist and run fine on the VM — only the NIST-curve ECDH piece is the browser-only exception |

(You *can* do everything in `pointycastle` alone — it also has `HKDFKeyDerivator` and `GCMBlockCipher`/AES — if you'd rather not pull in a second package. Either combination is fine; just don't reach for `package:cryptography`'s `EcdhP256` on a phone.)

### Protocol recap (same as §3, Dart-specific notes added)

1. Generate an ephemeral P-256 key pair with `pointycastle` (`ECKeyGenerator` + `ECKeyGeneratorParameters(ECDomainParameters('secp256r1'))`).
2. Encode your public key point as an **uncompressed X9.62 point** (`ECPublicKey.Q.getEncoded(false)` — the `false` means "not compressed", giving the same `0x04 || X || Y` 65-byte format the server and the React client use) and urlsafe-base64 it (standard `dart:convert` `base64Url`, strip `=` padding to match the server — though the server's decoder tolerates padding either way since it round-trips through Python's `base64.urlsafe_b64decode`, which handles padding automatically).
3. `POST` `{"client_public_key": "<that string>"}` to `/api/v1/session-key/`, same as the React client does in `payloadCrypto.ts`.
4. Decode `server_public_key` the same way, run `ECDHBasicAgreement.calculateAgreement(...)` to get the shared secret as a `BigInt` → convert to a fixed-length 32-byte big-endian byte array (pad left with zeros if the magnitude is short — this is the #1 subtle bug spot in hand-rolled ECDH ports, since a leading-zero byte silently disappears from a naive `BigInt`→bytes conversion and throws HKDF off).
5. Run `Hkdf(hmac: Hmac.sha256(), outputLength: 32).deriveKey(secretKey: ..., nonce: [], info: utf8.encode("hospital-crm-payload-v1"))` (pass an **empty** nonce/salt — matches the server's `salt=None`).
6. Use the resulting 32 bytes as an `AesGcm.with256bits()` secret key for encrypt/decrypt, exactly mirroring the `gcm2$` token format in §3 (12-byte random nonce prepended to ciphertext+tag, no AAD, urlsafe-base64, `gcm2$` prefix).
7. Send `X-Session-Id: <session_id>` on every request after that, same header the React client uses.

### Verify before wiring into the app: use the test vector

Don't plug a hand-ported ECDH/HKDF/AES-GCM implementation straight into real API calls and debug it against a live server — a wrong byte order or an off-by-one in the info string produces an opaque AES-GCM auth failure (§7 FAQ), not a helpful error message.

Instead, write **one unit test first** against [`docs/test-vectors/layer2-payload-encryption.json`](test-vectors/layer2-payload-encryption.json) — a fixed input/output pair generated directly from the server's own crypto code (`apps.core.payload_crypto`), with a successful round-trip through the server's real `decrypt_payload()` already verified when it was generated. It gives you fixed keys, the expected shared secret, the expected derived AES key, and an expected `gcm2$` token for a known plaintext. Your Dart port is correct once it:
- derives `shared_secret_hex` from the given key pair,
- derives `derived_aes_key_hex` from that via HKDF,
- decrypts `gcm2_token` back to the exact `plaintext_utf8`.

Only after that test passes should you wire the implementation into `initSession()`/request-signing code and test against a running server.

---

## 6. Testing manually (Postman / curl)

**Easiest option:** test against a server/env with `PAYLOAD_ENCRYPTION_ENABLED=False` (the local dev default). Everything is plain JSON — no extra steps.

**Testing a new client implementation's crypto correctness (either platform):** use [`docs/test-vectors/layer2-payload-encryption.json`](test-vectors/layer2-payload-encryption.json) — see §5b. It's a fixed, known-answer ECDH/HKDF/AES-GCM input-output pair generated from the server's own code, so you can confirm your port is byte-compatible without a running server or network calls at all.

**If you need to test against encryption-enabled (staging/prod):**
- The Postman collection ([`docs/postman/Hospital-CRM-ERP.postman_collection.json`](postman/Hospital-CRM-ERP.postman_collection.json)) already has a `session-key` request. There's no built-in Postman ECDH helper though — practically speaking, QA should exercise the encrypted path through the real frontend (browser DevTools → Network tab will show the `{"enc": "gcm2$..."}` bodies flowing), not by hand-building ECDH math in Postman.
- If you truly need a standalone script (e.g. for a smoke test), it's a handful of lines with Python's `cryptography` package (same library the server uses) — mirror `Backend/apps/core/payload_crypto.py` functions directly rather than reimplementing the math.

---

## 7. FAQ / gotchas

**Q: Do I need to encrypt file uploads (multipart/form-data)?**
No. The middleware only touches `request.body` for POST/PUT/PATCH, and only attempts response encryption when `Content-Type` is `application/json`. Multipart uploads and binary/blob downloads (PDFs etc., see `api.getBlob` in `client.ts`) are untouched by Layer 2.

**Q: My request works unencrypted but fails after I add `X-Session-Id`. Why?**
Either the session expired/was never created (→ 401, re-handshake) or your encrypted body is malformed/wrong-keyed (→ 400 "Encrypted payload could not be decrypted"). Check you're using the *current* session's key — a page refresh invalidates the old one client-side (though the server-side cache entry lingers until TTL).

**Q: Does Layer 2 replace HTTPS?**
No — it's defense-in-depth *underneath* TLS, mainly to keep payloads unreadable in browser DevTools / a compromised TLS-terminating proxy, not a substitute for TLS. Always run behind HTTPS in any real environment (`SECURE_SSL_REDIRECT=True` in `prod.py`).

**Q: I'm adding a new sensitive field to a model (e.g. another government ID). Does that belong in Layer 2 or Layer 3?**
Layer 3 (field-level DB encryption). Use `EncryptedCharField`/`EncryptedTextField`/`EncryptedJSONField` from `apps.core.fields` — see `apps/patients/models.py` (`national_id_number`, `insurance_policy_number`) for the existing pattern, including the blind-index approach for exact-match search on an encrypted column. Layer 2 is purely transport-of-the-moment; it doesn't change what's stored in the DB.

**Q: Where do the AES keys/secrets live, and do I need any of them as a client dev?**
No — that's the point of ECDH. You never need a shared secret handed to you; each browser session derives its own key live. The only thing ops needs to manage is `PAYLOAD_ENCRYPTION_ENABLED` (on/off) and the Layer-3 `FIELD_ENCRYPTION_KEY_V2`/`FIELD_HASH_KEY` env vars (those are an ops/infra concern, not something client code touches).

---

## 8. Source of truth

If this doc and the code disagree, the code wins — please update this doc in the same PR. Primary files:

- `Backend/apps/core/middleware.py` — `PayloadEncryptionMiddleware`, bypass list
- `Backend/apps/core/payload_crypto.py` — ECDH/HKDF/AES-GCM implementation + protocol docstring
- `Backend/apps/core/views.py` — `SessionKeyView` (`/api/v1/session-key/`)
- `Backend/apps/core/encryption.py` — Layer-3 field-level encryption primitives
- `Backend/config/settings/base.py` — `PAYLOAD_ENCRYPTION_ENABLED`, `FIELD_ENCRYPTION_KEY*`, `MIDDLEWARE` order
- `Frontend/src/api/payloadCrypto.ts`, `Frontend/src/api/client.ts`, `Frontend/src/main.tsx` — client reference implementation (React)
- `docs/test-vectors/layer2-payload-encryption.json` — known-answer test vector for verifying a new client port (Flutter or otherwise)
- `docs/SECURITY_COMPLIANCE.md` — compliance framing (why this exists, ISO 27001/DPDP context)
