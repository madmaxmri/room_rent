# RoomRent

Two Flask services connected to the same MySQL database:

- `app.py` - customer rental website.
- `adminsApp.py` - separate admin service with its own login, pages, CSS, and JavaScript.

## Run

Website service:

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Open `http://localhost:5000`.

Admin service:

```powershell
.\.venv\Scripts\Activate.ps1
python adminsApp.py
```

Open `http://localhost:5002/login`.

Production WSGI entry points:

```powershell
waitress-serve --listen=127.0.0.1:5000 wsgi_website:application
waitress-serve --listen=127.0.0.1:5002 wsgi_admin:application
waitress-serve --listen=127.0.0.1:5001 wsgi_operations:application
```

Run these behind a reverse proxy or hosting platform that terminates HTTPS.

In VS Code:

- `Terminal > Run Task > Run Website app (5000)`
- `Terminal > Run Task > Run Admin app (5002)`
- `Terminal > Run Task > Run Website and Admin`

## Database

Both services use the same MySQL settings from `.env` through `db.py`:

```text
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=...
MYSQL_DATABASE=room_rent
MYSQL_CONNECT_TIMEOUT_SECONDS=2
MYSQL_RETRY_DELAY_SECONDS=15
```

`db.py` creates the required tables if they do not exist. Website users are stored in `users`; admin users are stored separately in `admin_accounts`.
If MySQL is not reachable, the app uses demo data and retries the database after the configured retry delay.
When `APP_ENV=production`, `FLASK_ENV=production`, `APP_PRODUCTION=1`, or `PRODUCTION=1` is set, MySQL is fail-closed by default. Database unavailability raises an error instead of using demo/JSON fallback storage. Set `PRODUCTION_FAIL_CLOSED=0` only for an explicit non-production exception.

Admin accounts store `admin_id`, `admin_secret_key`, `password_hash`, `is_active`, and timestamps. Passwords are hashed, not saved as plaintext.
Admin accounts also store authenticator-app MFA fields: `mfa_secret` and `mfa_enabled`.

Seed or update the first admin by setting:

```text
ADMIN_ID=admin
ADMIN_PASSWORD=change-this-password
ADMIN_SECRET_KEY=change-this-secret-key
ADMIN_NAME=Operations Admin
ADMIN_PORT=5002
ADMIN_OTP_TTL_SECONDS=300
ADMIN_SIGNUP_CODE=optional-private-signup-code
ADMIN_ALLOWED_IPS=203.0.113.10,203.0.113.11
```

`ADMIN_SIGNUP_CODE` is optional in local development. In production it is required by startup validation, and admin signup requires it.

## Production Security

Set a production mode flag before deploying:

```text
APP_ENV=production
TRUSTED_HOSTS=roomrent.example.com,admin.roomrent.example.com
SECRET_KEY=<32+ char random secret>
ADMIN_APP_SECRET=<32+ char random secret>
SESSION_COOKIE_SECURE=1
MAX_CONTENT_LENGTH=1048576
FOOTPRINT_RETENTION_DAYS=365
```

Security controls now included in the app:

- CSRF protection on every POST form.
- Secure production cookie settings with HTTPS scheme support.
- Security headers including CSP, HSTS on secure requests, frame blocking, `nosniff`, and referrer policy.
- Trusted host validation when `TRUSTED_HOSTS` is set; required in production mode.
- In-process rate limits for login, OTP, MFA, inquiry, and API routes.
- Optional admin and operations IP allowlists with `ADMIN_ALLOWED_IPS` and `OPERATIONS_ALLOWED_IPS`.
- Server-side temporary auth challenge storage. Sensitive OTP/reset/signup/MFA setup payloads are no longer stored directly in Flask client-side session cookies.
- Production fail-closed database behavior.
- User footprint retention through `FOOTPRINT_RETENTION_DAYS`.

Infrastructure still required before launch:

- Put the services behind HTTPS with a production reverse proxy or platform proxy.
- Keep admin and operations services private through VPN, private networking, reverse-proxy auth, or IP allowlisting.
- Use a least-privilege MySQL user, private DB networking, encrypted backups, and tested restores.
- Use centralized logging/monitoring and alert on auth failures, rate limits, admin changes, and database errors.
- Scan dependencies regularly with `pip-audit`, Dependabot, Safety, or an equivalent tool.

## Admin Auth

Admin app routes:

- `GET/POST /login` - admin ID and password login.
- `GET/POST /signup` - create an admin account, then verify with OTP.
- `GET/POST /signup/verify` - confirm admin signup OTP.
- `GET/POST /reset-password` - start password reset using admin ID and admin secret key.
- `GET/POST /reset-password/verify` - verify OTP and set a new password.
- `GET/POST /mfa` - authenticator-code challenge after password login when MFA is enabled.
- `GET/POST /mfa/setup` - enable Microsoft Authenticator, Google Authenticator, or another TOTP app.
- `POST /mfa/disable` - disable authenticator MFA after confirming password.

OTP values are shown in the local UI as demo OTPs. A production system should deliver OTPs through email/SMS and should keep `ADMIN_SIGNUP_CODE` private.

## Separate Frontends

Website frontend:

- `templates/base.html`
- `templates/index.html`
- `templates/property.html`
- `templates/login.html`
- `templates/signup.html`
- `static/css/styles.css`
- `static/js/app.js`

Admin frontend:

- `templates/admin_login.html`
- `templates/admin_signup.html`
- `templates/admin_otp_verify.html`
- `templates/admin_reset_request.html`
- `templates/admin_reset_verify.html`
- `templates/admin_mfa_challenge.html`
- `templates/admin_mfa_setup.html`
- `templates/admin_dashboard.html`
- `static/css/admin.css`
- `static/js/admin.js`

## API

Website service, default `http://localhost:5000`:

- `GET /api/storage`
- `GET /api/listings`
- `GET /api/listings/<id>`
- `GET /api/listings/<id>/recommendations`

Admin service, default `http://localhost:5002`:

- `GET /api/storage`
- `GET /api/admin/me`
- `GET /api/admin/dashboard`
- `GET /api/admin/analytics`
