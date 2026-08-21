import base64
import hmac
import os
import secrets
import struct
import time
from functools import wraps
from urllib.parse import quote

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from analytics import admin_analytics_payload
from auth_store import list_users
from db import (
    SQL_UNAVAILABLE,
    find_admin_account_by_admin_id,
    find_admin_account_by_record_id,
    get_operations_dashboard,
    initialize_database,
    load_env,
    mark_admin_login,
    storage_label,
    update_admin_mfa,
    update_admin_password,
    upsert_admin_account,
)
from security import (
    configure_security,
    install_csrf,
    install_ip_allowlist,
    is_production,
    rate_limit,
    validate_production_config,
)
from server_state import delete_challenge, load_challenge, save_challenge


load_env()
validate_production_config(["SECRET_KEY", "ADMIN_APP_SECRET", "ADMIN_SIGNUP_CODE"])
initialize_database()

admins_app = Flask(__name__)
admins_app.config["SECRET_KEY"] = os.getenv(
    "ADMIN_APP_SECRET", os.getenv("SECRET_KEY", "room-rent-admin-dev-secret")
)
admins_app.config["SESSION_COOKIE_NAME"] = "room_rent_admin_session"
admins_app.config["SESSION_COOKIE_HTTPONLY"] = True
admins_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
admins_app.config["PREFERRED_URL_SCHEME"] = "http"
configure_security(
    admins_app,
    secret_env="ADMIN_APP_SECRET",
    cookie_name="room_rent_admin_session",
    admin=True,
)
install_ip_allowlist(admins_app, "ADMIN_ALLOWED_IPS")
install_csrf(admins_app)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ADMIN_OTP_TTL_SECONDS = _env_int("ADMIN_OTP_TTL_SECONDS", 300)
ADMIN_TOTP_ISSUER = os.getenv("ADMIN_TOTP_ISSUER", "RoomRent Admin")
ADMIN_MFA_CHALLENGE_TTL_SECONDS = 180
ADMIN_MFA_SETUP_TTL_SECONDS = _env_int("ADMIN_MFA_SETUP_TTL_SECONDS", 600)
ADMIN_SIGNUP_CHALLENGE = "admin_signup_otp"
ADMIN_RESET_CHALLENGE = "admin_reset_otp"
ADMIN_LOGIN_MFA_CHALLENGE = "admin_login_mfa"
ADMIN_MFA_SETUP_CHALLENGE = "admin_mfa_setup"


def _admin_base_url():
    configured_url = os.getenv("ADMIN_BASE_URL")
    if configured_url:
        return configured_url.rstrip("/")

    port = _env_int("ADMIN_PORT", 5002)
    return f"http://localhost:{port}"


def _initials(name, fallback="A"):
    parts = [part for part in (name or "").strip().split() if part]
    if len(parts) >= 2:
        return f"{parts[0][0]}{parts[-1][0]}".upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return fallback[:2].upper() or "A"


def _admin_profile(admin):
    if not admin:
        return None

    display_name = admin.get("display_name") or admin.get("admin_id") or "Admin"
    return {
        "id": admin.get("admin_id"),
        "name": display_name,
        "email": "",
        "phone": "",
        "avatar_url": "",
        "auth_method": "admin",
        "auth_label": "Admin account",
        "initials": _initials(display_name, admin.get("admin_id") or "A"),
        "verified": bool(admin.get("is_active")),
        "created_at": admin.get("created_at") or "",
        "last_login_at": admin.get("last_login_at") or "",
        "mfa_enabled": bool(admin.get("mfa_enabled")),
    }


def _current_admin():
    return find_admin_account_by_record_id(session.get("admin_record_id"))


def _current_admin_profile():
    admin = _current_admin()
    if admin is SQL_UNAVAILABLE:
        return None
    return _admin_profile(admin)


def _complete_admin_login(admin):
    session.clear()
    session["admin_record_id"] = admin["id"]
    session["admin_id"] = admin["admin_id"]
    mark_admin_login(admin["id"])


def _generate_otp():
    return f"{secrets.randbelow(1000000):06d}"


def _otp_expired(payload):
    return time.time() > payload.get("expires_at", 0)


def _valid_password_pair(password, confirm_password):
    if len(password or "") < 8:
        flash("Password must be at least 8 characters.", "error")
        return False
    if len(password or "") > 512:
        flash("Password is too long.", "error")
        return False
    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return False
    return True


def _form_text(name, limit):
    value = request.form.get(name, "").strip()
    return value[:limit], len(value) > limit


def _signup_code_valid():
    expected_code = os.getenv("ADMIN_SIGNUP_CODE", "").strip()
    if not expected_code:
        return not is_production()
    return hmac.compare_digest(
        request.form.get("signup_code", "").strip(),
        expected_code,
    )


def _generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_key(secret):
    normalized = "".join((secret or "").upper().split())
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def _totp_code(secret, counter):
    key = _totp_key(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1000000:06d}"


def _verify_totp(secret, code, window=1):
    clean_code = "".join((code or "").split())
    if not clean_code.isdigit() or len(clean_code) != 6:
        return False

    counter = int(time.time() // 30)
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_totp_code(secret, counter + offset), clean_code):
            return True
    return False


def _otpauth_uri(admin, secret):
    label = quote(f"{ADMIN_TOTP_ISSUER}:{admin['admin_id']}")
    issuer = quote(ADMIN_TOTP_ISSUER)
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&digits=6&period=30"


def _seed_configured_admin():
    admin_id = os.getenv("ADMIN_ID") or os.getenv("ADMIN_LOGIN_ID")
    password = os.getenv("ADMIN_PASSWORD")
    if not admin_id or not password:
        return None

    return upsert_admin_account(
        admin_id=admin_id,
        password_hash=generate_password_hash(password),
        admin_secret_key=os.getenv("ADMIN_SECRET_KEY"),
        display_name=os.getenv("ADMIN_NAME", "Admin"),
    )


ADMIN_SEED_RESULT = _seed_configured_admin()


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        admin = _current_admin()
        if admin is not SQL_UNAVAILABLE and admin and admin.get("is_active"):
            return view(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"error": "Admin authentication required"}), 401

        return redirect(url_for("admin_login"))

    return wrapped_view


@admins_app.context_processor
def inject_admin_context():
    return {
        "admin_home_url": url_for("admin_dashboard"),
        "admin_login_url": url_for("admin_login"),
        "admin_signup_url": url_for("admin_signup"),
        "admin_reset_url": url_for("admin_reset_password"),
        "admin_logout_url": url_for("admin_logout"),
    }


@admins_app.route("/", methods=["GET"])
def home():
    if _current_admin_profile():
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))


@admins_app.route("/login", methods=["GET", "POST"])
@rate_limit("admin_login", limit=5, window_seconds=300, include_form=("admin_id",))
def admin_login():
    if _current_admin_profile():
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        admin_id, admin_id_too_long = _form_text("admin_id", 120)
        password = request.form.get("password", "")
        admin = None if admin_id_too_long else find_admin_account_by_admin_id(admin_id)

        if admin_id_too_long:
            flash("Invalid admin ID or password.", "error")
        elif admin is SQL_UNAVAILABLE:
            flash("Admin database is unavailable.", "error")
        elif (
            not admin
            or not admin.get("is_active")
            or not check_password_hash(admin.get("password_hash", ""), password)
        ):
            flash("Invalid admin ID or password.", "error")
        elif admin.get("mfa_enabled"):
            session.clear()
            session["pending_admin_mfa_id"] = save_challenge(
                ADMIN_LOGIN_MFA_CHALLENGE,
                {"admin_record_id": admin["id"]},
                ADMIN_MFA_CHALLENGE_TTL_SECONDS,
            )
            return redirect(url_for("admin_mfa_challenge"))
        else:
            _complete_admin_login(admin)
            flash("Admin signed in.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template(
        "admin_login.html",
        admin_seeded=ADMIN_SEED_RESULT is not None
        and ADMIN_SEED_RESULT is not SQL_UNAVAILABLE,
        admin_storage_label=storage_label(),
    )


@admins_app.route("/mfa", methods=["GET", "POST"])
@rate_limit("admin_mfa", limit=8, window_seconds=300)
def admin_mfa_challenge():
    pending_mfa_id = session.get("pending_admin_mfa_id")
    pending_mfa = load_challenge(pending_mfa_id, ADMIN_LOGIN_MFA_CHALLENGE)
    if not pending_mfa or time.time() > pending_mfa.get("expires_at", 0):
        delete_challenge(pending_mfa_id)
        session.pop("pending_admin_mfa_id", None)
        flash("MFA session expired. Sign in again.", "error")
        return redirect(url_for("admin_login"))

    admin = find_admin_account_by_record_id(pending_mfa.get("admin_record_id"))
    if admin is SQL_UNAVAILABLE or not admin or not admin.get("mfa_enabled"):
        delete_challenge(pending_mfa_id)
        session.pop("pending_admin_mfa_id", None)
        flash("MFA could not be verified.", "error")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        if _verify_totp(admin.get("mfa_secret"), request.form.get("totp", "")):
            delete_challenge(pending_mfa_id)
            _complete_admin_login(admin)
            flash("Admin signed in.", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid authenticator code.", "error")

    return render_template("admin_mfa_challenge.html", admin=admin)


@admins_app.route("/signup", methods=["GET", "POST"])
@rate_limit("admin_signup", limit=3, window_seconds=600, include_form=("admin_id",))
def admin_signup():
    if _current_admin_profile():
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        admin_id, admin_id_too_long = _form_text("admin_id", 120)
        display_name, display_name_too_long = _form_text("display_name", 160)
        admin_secret_key, secret_too_long = _form_text("admin_secret_key", 128)
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        existing_admin = None if admin_id_too_long else find_admin_account_by_admin_id(admin_id)
        if admin_id_too_long or display_name_too_long or secret_too_long:
            flash("One or more fields are too long.", "error")
        elif existing_admin is SQL_UNAVAILABLE:
            flash("Admin database is unavailable.", "error")
        elif existing_admin:
            flash("This admin ID already exists.", "error")
        elif not admin_id or not display_name or not admin_secret_key:
            flash("Admin ID, name, and secret key are required.", "error")
        elif not _signup_code_valid():
            flash("Invalid signup code.", "error")
        elif _valid_password_pair(password, confirm_password):
            otp = _generate_otp()
            session["pending_admin_signup_id"] = save_challenge(
                ADMIN_SIGNUP_CHALLENGE,
                {
                    "admin_id": admin_id,
                    "display_name": display_name,
                    "admin_secret_key": admin_secret_key,
                    "password_hash": generate_password_hash(password),
                    "otp_hash": generate_password_hash(otp),
                },
                ADMIN_OTP_TTL_SECONDS,
            )
            session["admin_demo_otp"] = otp
            flash(f"Demo admin signup OTP: {otp}", "info")
            return redirect(url_for("admin_signup_verify"))

    return render_template(
        "admin_signup.html",
        signup_code_required=bool(os.getenv("ADMIN_SIGNUP_CODE", "").strip()) or is_production(),
    )


@admins_app.route("/signup/verify", methods=["GET", "POST"])
@rate_limit("admin_signup_verify", limit=8, window_seconds=300)
def admin_signup_verify():
    pending_signup_id = session.get("pending_admin_signup_id")
    pending_signup = load_challenge(pending_signup_id, ADMIN_SIGNUP_CHALLENGE)
    if not pending_signup:
        session.pop("pending_admin_signup_id", None)
        session.pop("admin_demo_otp", None)
        flash("Start admin signup first.", "info")
        return redirect(url_for("admin_signup"))

    if request.method == "POST":
        if _otp_expired(pending_signup):
            delete_challenge(pending_signup_id)
            session.pop("pending_admin_signup_id", None)
            session.pop("admin_demo_otp", None)
            flash("OTP expired. Start signup again.", "error")
            return redirect(url_for("admin_signup"))

        if not check_password_hash(
            pending_signup.get("otp_hash", ""),
            request.form.get("otp", "").strip(),
        ):
            flash("Invalid OTP.", "error")
        else:
            admin = upsert_admin_account(
                admin_id=pending_signup["admin_id"],
                password_hash=pending_signup["password_hash"],
                admin_secret_key=pending_signup["admin_secret_key"],
                display_name=pending_signup["display_name"],
            )
            delete_challenge(pending_signup_id)
            session.pop("pending_admin_signup_id", None)
            session.pop("admin_demo_otp", None)
            if admin is SQL_UNAVAILABLE:
                flash("Admin database is unavailable.", "error")
                return redirect(url_for("admin_signup"))
            _complete_admin_login(admin)
            flash("Admin account created.", "success")
            return redirect(url_for("admin_mfa_setup"))

    return render_template(
        "admin_otp_verify.html",
        title="Verify admin signup",
        heading="Enter signup OTP",
        form_action=url_for("admin_signup_verify"),
        demo_otp=session.get("admin_demo_otp"),
        back_url=url_for("admin_signup"),
    )


@admins_app.route("/reset-password", methods=["GET", "POST"])
@rate_limit("admin_reset_request", limit=5, window_seconds=300, include_form=("admin_id",))
def admin_reset_password():
    if request.method == "POST":
        admin_id, admin_id_too_long = _form_text("admin_id", 120)
        admin_secret_key, secret_too_long = _form_text("admin_secret_key", 128)
        admin = None if admin_id_too_long else find_admin_account_by_admin_id(admin_id)

        if admin_id_too_long or secret_too_long:
            flash("Admin ID or secret key is invalid.", "error")
        elif admin is SQL_UNAVAILABLE:
            flash("Admin database is unavailable.", "error")
        elif (
            not admin
            or not admin.get("is_active")
            or not hmac.compare_digest(
                admin.get("admin_secret_key", ""),
                admin_secret_key,
            )
        ):
            flash("Admin ID or secret key is invalid.", "error")
        else:
            otp = _generate_otp()
            session["pending_admin_reset_id"] = save_challenge(
                ADMIN_RESET_CHALLENGE,
                {
                    "admin_record_id": admin["id"],
                    "otp_hash": generate_password_hash(otp),
                },
                ADMIN_OTP_TTL_SECONDS,
            )
            session["admin_demo_otp"] = otp
            flash(f"Demo password reset OTP: {otp}", "info")
            return redirect(url_for("admin_reset_verify"))

    return render_template("admin_reset_request.html")


@admins_app.route("/reset-password/verify", methods=["GET", "POST"])
@rate_limit("admin_reset_verify", limit=8, window_seconds=300)
def admin_reset_verify():
    pending_reset_id = session.get("pending_admin_reset_id")
    pending_reset = load_challenge(pending_reset_id, ADMIN_RESET_CHALLENGE)
    if not pending_reset:
        session.pop("pending_admin_reset_id", None)
        session.pop("admin_demo_otp", None)
        flash("Start password reset first.", "info")
        return redirect(url_for("admin_reset_password"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if _otp_expired(pending_reset):
            delete_challenge(pending_reset_id)
            session.pop("pending_admin_reset_id", None)
            session.pop("admin_demo_otp", None)
            flash("OTP expired. Start password reset again.", "error")
            return redirect(url_for("admin_reset_password"))

        if not check_password_hash(pending_reset.get("otp_hash", ""), otp):
            flash("Invalid OTP.", "error")
        elif _valid_password_pair(password, confirm_password):
            updated = update_admin_password(
                pending_reset["admin_record_id"],
                generate_password_hash(password),
            )
            delete_challenge(pending_reset_id)
            session.pop("pending_admin_reset_id", None)
            session.pop("admin_demo_otp", None)
            if updated is SQL_UNAVAILABLE:
                flash("Admin database is unavailable.", "error")
            elif updated:
                flash("Password reset complete. Sign in with the new password.", "success")
                return redirect(url_for("admin_login"))
            else:
                flash("Password could not be reset.", "error")

    return render_template(
        "admin_reset_verify.html",
        demo_otp=session.get("admin_demo_otp"),
    )


@admins_app.route("/mfa/setup", methods=["GET", "POST"])
@admin_required
@rate_limit("admin_mfa_setup", limit=8, window_seconds=300)
def admin_mfa_setup():
    admin = _current_admin()
    if admin is SQL_UNAVAILABLE or not admin:
        flash("Admin database is unavailable.", "error")
        return redirect(url_for("admin_login"))

    if admin.get("mfa_enabled"):
        return render_template(
            "admin_mfa_setup.html",
            current_admin=_admin_profile(admin),
            mfa_enabled=True,
            secret=admin.get("mfa_secret"),
            otpauth_uri=_otpauth_uri(admin, admin.get("mfa_secret")),
        )

    pending_secret_id = session.get("pending_admin_mfa_setup_id")
    pending_setup = load_challenge(pending_secret_id, ADMIN_MFA_SETUP_CHALLENGE)
    if pending_setup and pending_setup.get("admin_record_id") != admin["id"]:
        delete_challenge(pending_secret_id)
        session.pop("pending_admin_mfa_setup_id", None)
        pending_setup = None
    if not pending_setup:
        pending_secret = _generate_totp_secret()
        pending_secret_id = save_challenge(
            ADMIN_MFA_SETUP_CHALLENGE,
            {"admin_record_id": admin["id"], "secret": pending_secret},
            ADMIN_MFA_SETUP_TTL_SECONDS,
        )
        session["pending_admin_mfa_setup_id"] = pending_secret_id
    else:
        pending_secret = pending_setup.get("secret") or _generate_totp_secret()

    if request.method == "POST":
        if pending_setup and pending_setup.get("admin_record_id") != admin["id"]:
            delete_challenge(pending_secret_id)
            session.pop("pending_admin_mfa_setup_id", None)
            flash("MFA setup expired. Start again.", "error")
            return redirect(url_for("admin_mfa_setup"))
        if _verify_totp(pending_secret, request.form.get("totp", "")):
            updated = update_admin_mfa(admin["id"], pending_secret, True)
            if updated is SQL_UNAVAILABLE:
                flash("Admin database is unavailable.", "error")
            elif updated:
                delete_challenge(pending_secret_id)
                session.pop("pending_admin_mfa_setup_id", None)
                flash("Authenticator MFA is enabled.", "success")
                return redirect(url_for("admin_dashboard"))
            else:
                flash("MFA could not be enabled.", "error")
        else:
            flash("Invalid authenticator code.", "error")

    return render_template(
        "admin_mfa_setup.html",
        current_admin=_admin_profile(admin),
        mfa_enabled=False,
        secret=pending_secret,
        otpauth_uri=_otpauth_uri(admin, pending_secret),
    )


@admins_app.post("/mfa/disable")
@admin_required
@rate_limit("admin_mfa_disable", limit=5, window_seconds=300)
def admin_mfa_disable():
    admin = _current_admin()
    password = request.form.get("password", "")
    if admin is SQL_UNAVAILABLE or not admin:
        flash("Admin database is unavailable.", "error")
    elif not check_password_hash(admin.get("password_hash", ""), password):
        flash("Password is incorrect.", "error")
    else:
        updated = update_admin_mfa(admin["id"], None, False)
        if updated is SQL_UNAVAILABLE:
            flash("Admin database is unavailable.", "error")
        elif updated:
            flash("Authenticator MFA is disabled.", "success")
        else:
            flash("MFA could not be disabled.", "error")
    return redirect(url_for("admin_mfa_setup"))


@admins_app.get("/dashboard")
@admin_required
def admin_dashboard():
    admin = _current_admin()
    return render_template(
        "admin_dashboard.html",
        current_admin=_admin_profile(admin),
        dashboard_api_url=url_for("api_admin_dashboard"),
        analytics_api_url=url_for("api_admin_analytics"),
        storage_api_url=url_for("api_storage"),
        mfa_setup_url=url_for("admin_mfa_setup"),
    )


@admins_app.get("/api/storage")
@rate_limit("admin_api_storage", limit=120, window_seconds=60, methods=("GET",))
def api_storage():
    return jsonify({"storage_label": storage_label()})


@admins_app.get("/api/admin/dashboard")
@admin_required
@rate_limit("admin_api_dashboard", limit=120, window_seconds=60, methods=("GET",))
def api_admin_dashboard():
    return jsonify(get_operations_dashboard(list_users()))


@admins_app.get("/api/admin/analytics")
@admin_required
@rate_limit("admin_api_analytics", limit=60, window_seconds=60, methods=("GET",))
def api_admin_analytics():
    return jsonify(admin_analytics_payload())


@admins_app.get("/api/operations/dashboard")
@admin_required
@rate_limit("admin_api_operations_legacy", limit=120, window_seconds=60, methods=("GET",))
def api_operations_dashboard_legacy():
    return redirect(url_for("api_admin_dashboard"), code=308)


@admins_app.get("/api/admin/me")
@admin_required
@rate_limit("admin_api_me", limit=120, window_seconds=60, methods=("GET",))
def api_admin_me():
    admin = _current_admin()
    if admin is SQL_UNAVAILABLE or not admin:
        return jsonify({"error": "Admin authentication required"}), 401
    return jsonify({"admin": _admin_profile(admin)})


@admins_app.get("/logout")
def admin_logout():
    session.clear()
    flash("Admin signed out.", "info")
    return redirect(url_for("admin_login"))


@admins_app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("admin_404.html"), 404


if __name__ == "__main__":
    admins_app.run(
        host=os.getenv("ADMIN_HOST", "127.0.0.1"),
        port=_env_int("ADMIN_PORT", 5002),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
