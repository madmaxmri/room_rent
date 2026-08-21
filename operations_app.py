import os
from functools import wraps

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from auth_store import find_user_by_id, list_users, public_user
from db import get_operations_dashboard, initialize_database, load_env, storage_label
from security import (
    configure_security,
    install_csrf,
    install_ip_allowlist,
    rate_limit,
    validate_production_config,
)


load_env()
validate_production_config(["SECRET_KEY"])
initialize_database()

operations_app = Flask(__name__)
operations_app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY", "room-rent-dev-secret"
)
operations_app.config["SESSION_COOKIE_HTTPONLY"] = True
operations_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
operations_app.config["PREFERRED_URL_SCHEME"] = "http"
configure_security(operations_app, secret_env="SECRET_KEY")
install_ip_allowlist(operations_app, "OPERATIONS_ALLOWED_IPS")
install_csrf(operations_app)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_values(*names, lower=False):
    values = set()
    for name in names:
        raw_value = os.getenv(name, "")
        for item in raw_value.split(","):
            value = item.strip()
            if value:
                values.add(value.lower() if lower else value)
    return values


def _main_site_url():
    return (os.getenv("MAIN_SITE_URL") or "http://localhost:5000").rstrip("/")


def _operations_base_url():
    configured_url = os.getenv("OPERATIONS_BASE_URL") or os.getenv("OPERATION_BASE_URL")
    if configured_url:
        return configured_url.rstrip("/")

    port = _env_int("OPERATIONS_PORT", _env_int("OPERATION_PORT", 5001))
    return f"http://localhost:{port}"


def _current_user():
    return public_user(find_user_by_id(session.get("user_id")))


def _is_operation_user(user):
    if not user:
        return False

    allowed_ids = _env_values("OPERATION_USER_IDS", "OPERATIONS_USER_IDS")
    allowed_emails = _env_values(
        "OPERATION_EMAILS", "OPERATIONS_EMAILS", lower=True
    )
    allowed_phones = _env_values("OPERATION_PHONES", "OPERATIONS_PHONES")

    if not allowed_ids and not allowed_emails and not allowed_phones:
        return True

    return (
        user.get("id") in allowed_ids
        or (user.get("email") or "").lower() in allowed_emails
        or user.get("phone") in allowed_phones
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if _current_user():
            return view(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401

        return redirect(f"{_main_site_url()}/login")

    return wrapped_view


def operation_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if _is_operation_user(_current_user()):
            return view(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"error": "Operations access required"}), 403

        flash("Operations dashboard access is restricted.", "error")
        return redirect(_main_site_url())

    return wrapped_view


@operations_app.context_processor
def inject_operations_context():
    current_user = _current_user()
    return {
        "current_user": current_user,
        "google_oauth_enabled": False,
        "operation_dashboard_enabled": _is_operation_user(current_user),
        "operation_dashboard_url": f"{_operations_base_url()}/operations",
        "site_home_url": _main_site_url(),
        "login_url": f"{_main_site_url()}/login",
        "signup_url": f"{_main_site_url()}/signup",
        "logout_url": url_for("logout"),
        "storage_api_url": url_for("api_storage"),
    }


@operations_app.get("/")
def home():
    return redirect(url_for("operations_dashboard"))


@operations_app.get("/operations")
@login_required
@operation_required
def operations_dashboard():
    return render_template("operations.html")


@operations_app.get("/api/storage")
@rate_limit("operations_api_storage", limit=120, window_seconds=60, methods=("GET",))
def api_storage():
    return jsonify({"storage_label": storage_label()})


@operations_app.get("/api/operations/dashboard")
@login_required
@operation_required
@rate_limit("operations_api_dashboard", limit=120, window_seconds=60, methods=("GET",))
def api_operations_dashboard():
    return jsonify(get_operations_dashboard(list_users()))


@operations_app.get("/logout")
def logout():
    session.clear()
    return redirect(f"{_main_site_url()}/login")


@operations_app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404


if __name__ == "__main__":
    operations_app.run(
        host=os.getenv("OPERATIONS_HOST", "127.0.0.1"),
        port=_env_int("OPERATIONS_PORT", _env_int("OPERATION_PORT", 5001)),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
