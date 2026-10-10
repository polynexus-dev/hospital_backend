# On-premise deployment

One hospital on its own server with its own PostgreSQL database, governed by a
signed license file. The application ships as two images with no readable
backend source: the backend is Cython-compiled, and the frontend is a minified,
obfuscated bundle.

## Platform side (Polynexus)

1. **Once:** create the signing key pair.
   ```
   cd Backend
   python manage.py generate_license_keypair --private-key-out /secure/path/license_signing_key.pem
   ```
   Commit the updated `apps/licensing/public_key.py`. Put the private key in the
   SaaS deployment's secrets as `LICENSE_SIGNING_KEY` (PEM text) or
   `LICENSE_SIGNING_KEY_PATH`. Never commit the private key and never ship it to a
   hospital. Rotating the key pair invalidates every license already issued.
2. **Each release:** build and push the images.
   ```
   docker build -f Backend/Dockerfile.backend.compiled -t registry.polynexus.in/hms/hms-backend:<version> Backend
   docker build -f Frontend/Dockerfile.frontend        -t registry.polynexus.in/hms/hms-frontend:<version> Frontend
   ```
3. **Each customer:** in the SaaS console, open the tenant and click **📜 License**.
   Paste the fingerprint the hospital sends, choose the expiry date, modules and
   user/bed limits, then click **Generate & Download License**. Revoking a license
   stops re-downloads, but an offline server keeps using it until it expires or a
   newer license replaces it.

## Hospital side

Copy this folder to the server and run `./install.sh`. The script:

1. generates `.env` with fresh secrets;
2. pulls the images;
3. prints the machine fingerprint to send to support;
4. installs `license/hospital.lic`;
5. runs migrations and creates the hospital and its owner account;
6. starts everything.

## License behaviour

| State | Effect |
|---|---|
| valid | Normal operation |
| expiring soon (30 days or less) | Banner for admins; `X-License-Status: expiring_soon` header |
| grace period (after expiry, `grace_period_days`) | Still writable, red banner |
| expired / tampered / missing | Read-only: GET requests work, saves are refused (402 or 403) |
| wrong machine | The server refuses to start |

The license also limits the hospital to its licensed modules, its active-user
count and its bed count. The system clock is checked against the latest time the
installation has seen, so setting the clock back is detected.

**Renewing:** upload the new file in **Settings → License**, or replace
`license/hospital.lic` and restart `backend` and `worker`.
