import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, jsonify, redirect, request, session


_RATE_LIMITS = defaultdict(deque)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "production"}


def is_production():
    return (
        env_bool("APP_PRODUCTION")
        or env_bool("PRODUCTION")
        or os.getenv("APP_ENV", "").strip().lower() == "production"
        or os.getenv("FLASK_ENV", "").strip().lower() == "production"
    )


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _trusted_hosts():
    raw_hosts = os.getenv("TRUSTED_HOSTS", "")
    return {item.strip().lower() for item in raw_hosts.split(",") if item.strip()}


def _env_set(name):
    raw_values = os.getenv(name, "")
    return {item.strip() for item in raw_values.split(",") if item.strip()}


def _host_allowed(host):
    trusted_hosts = _trusted_hosts()
    if not trusted_hosts:
        return not is_production()

    host = (host or "").lower()
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    return host in trusted_hosts or hostname in trusted_hosts


def _request_origin_allowed():
    expected_hosts = _trusted_hosts()
    if not expected_hosts:
        expected_hosts = {request.host.lower()}

    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return True

    parsed = urlparse(origin)
    origin_host = (parsed.netloc or "").lower()
    origin_hostname = origin_host.rsplit(":", 1)[0] if ":" in origin_host else origin_host
    return origin_host in expected_hosts or origin_hostname in expected_hosts


def validate_production_config(secret_names):
    if not is_production():
        return

    missing = [name for name in secret_names if not os.getenv(name)]
    weak = [
        name
        for name in secret_names
        if os.getenv(name) and len(os.getenv(name, "")) < 32
    ]
    if missing or weak:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if weak:
            details.append("too short: " + ", ".join(weak))
        raise RuntimeError("Production secrets are not ready (" + "; ".join(details) + ")")

    if not _trusted_hosts():
        raise RuntimeError("TRUSTED_HOSTS must be set in production.")


def configure_security(app, *, secret_env="SECRET_KEY", cookie_name=None, admin=False):
    production = is_production()
    secure_cookie = env_bool("SESSION_COOKIE_SECURE", production)

    configured_secret = os.getenv(secret_env) or os.getenv("SECRET_KEY")
    if configured_secret:
        app.config["SECRET_KEY"] = configured_secret

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=secure_cookie,
        SESSION_COOKIE_SAMESITE=os.getenv(
            "SESSION_COOKIE_SAMESITE",
            "Strict" if admin and production else "Lax",
        ),
        PREFERRED_URL_SCHEME="https" if secure_cookie else "http",
        MAX_CONTENT_LENGTH=_env_int("MAX_CONTENT_LENGTH", 1024 * 1024),
        MAX_FORM_MEMORY_SIZE=_env_int("MAX_FORM_MEMORY_SIZE", 64 * 1024),
        MAX_FORM_PARTS=_env_int("MAX_FORM_PARTS", 100),
        PERMANENT_SESSION_LIFETIME=_env_int("SESSION_LIFETIME_SECONDS", 3600),
    )
    if cookie_name:
        app.config["SESSION_COOKIE_NAME"] = cookie_name

    @app.before_request
    def _enforce_host_and_origin():
        if not _host_allowed(request.host):
            abort(400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _request_origin_allowed():
            abort(403)

    @app.after_request
    def _set_security_headers(response):
        csp = os.getenv(
            "CONTENT_SECURITY_POLICY",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'",
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if secure_cookie or request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                os.getenv(
                    "STRICT_TRANSPORT_SECURITY",
                    "max-age=31536000; includeSubDomains",
                ),
            )
        return response


def install_ip_allowlist(app, env_name):
    @app.before_request
    def _enforce_ip_allowlist():
        allowed_ips = _env_set(env_name)
        if not allowed_ips:
            return None
        if _client_ip() in allowed_ips:
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Access denied"}), 403
        abort(403)


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Invalid CSRF token"}), 400
    flash("Security check failed. Please submit the form again.", "error")
    return redirect(request.referrer or request.path or "/")


def install_csrf(app):
    @app.context_processor
    def _inject_csrf_token():
        return {"csrf_token": csrf_token}

    @app.before_request
    def _verify_csrf_token():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None

        expected = session.get("_csrf_token")
        supplied = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
        if expected and supplied and hmac.compare_digest(expected, supplied):
            return None
        return _csrf_response()


def check_rate_limit(key, limit, window_seconds):
    now = time.monotonic()
    bucket = _RATE_LIMITS[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def rate_limit(scope, *, limit=10, window_seconds=60, include_form=(), methods=("POST",)):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if methods and request.method not in methods:
                return view(*args, **kwargs)
            parts = [scope, _client_ip()]
            for field in include_form:
                parts.append((request.form.get(field) or "").strip().lower())
            if not check_rate_limit(":".join(parts), limit, window_seconds):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Too many requests"}), 429
                flash("Too many attempts. Please wait and try again.", "error")
                return redirect(request.referrer or request.path or "/")
            return view(*args, **kwargs)

        return wrapped

    return decorator
