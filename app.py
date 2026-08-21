import os
import secrets
import time
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth_store import (
    find_user_by_id,
    find_user_by_phone,
    normalize_phone,
    public_user,
    upsert_google_user,
    upsert_phone_user,
)
from db import (
    create_inquiry,
    get_listing,
    initialize_database,
    load_env,
    record_user_footprint,
    search_listings,
    storage_label,
)
from recommender import recommended_listings
from security import configure_security, install_csrf, rate_limit, validate_production_config
from server_state import delete_challenge, load_challenge, save_challenge

try:
    from authlib.integrations.flask_client import OAuth
except Exception:  # pragma: no cover - Google sign-in stays optional.
    OAuth = None


load_env()
validate_production_config(["SECRET_KEY"])
initialize_database()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "room-rent-dev-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PREFERRED_URL_SCHEME"] = "http"
configure_security(app, secret_env="SECRET_KEY")
install_csrf(app)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


OTP_TTL_SECONDS = _env_int("OTP_TTL_SECONDS", 300)
OTP_DEMO_MODE = os.getenv("OTP_DEMO_MODE", "1") != "0"
USER_OTP_CHALLENGE = "user_phone_otp"

oauth = OAuth(app) if OAuth is not None else None
google_oauth = None

if oauth and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
    google_oauth = oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        access_token_url="https://oauth2.googleapis.com/token",
        api_base_url="https://openidconnect.googleapis.com/v1/",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        id_token_signing_alg_values_supported=["RS256"],
        client_kwargs={"scope": "openid email profile"},
    )


PUBLIC_ENDPOINTS = {
    "login",
    "signup",
    "verify_otp",
    "google_login",
    "google_callback",
    "static",
}

TRACKING_EXCLUDED_ENDPOINTS = PUBLIC_ENDPOINTS | {
    "api_storage",
    "static",
}


def _filters_from_request():
    def query_value(name, limit=80):
        return request.args.get(name, "").strip()[:limit]

    return {
        "city": query_value("city", 120),
        "property_type": query_value("property_type", 80),
        "furnished_status": query_value("furnished_status", 80),
        "min_price": query_value("min_price", 12),
        "max_price": query_value("max_price", 12),
        "bedrooms": query_value("bedrooms", 4),
    }


def _form_text(name, limit):
    value = request.form.get(name, "").strip()
    return value[:limit], len(value) > limit


def _email_valid(email):
    return 3 <= len(email) <= 254 and "@" in email and "." in email.rsplit("@", 1)[-1]


def _current_user():
    return public_user(find_user_by_id(session.get("user_id")))


def _normalize_next_url(next_url, default_endpoint="home"):
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for(default_endpoint)


def _safe_next(default_endpoint="home"):
    return _normalize_next_url(
        request.values.get("next") or session.pop("oauth_next", None),
        default_endpoint=default_endpoint,
    )


def _login_user(user):
    session.clear()
    session["user_id"] = user["id"]
    record_user_footprint(_footprint_payload(user, "login"))


def _google_enabled():
    return google_oauth is not None


def _google_redirect_uri():
    return os.getenv("GOOGLE_REDIRECT_URI") or url_for(
        "google_callback", _external=True
    )


def _google_redirect_host():
    redirect_uri = _google_redirect_uri()
    return urlsplit(redirect_uri).netloc


def _canonical_local_url():
    if request.host != "127.0.0.1:5000":
        return None

    google_host = _google_redirect_host()
    if google_host != "localhost:5000":
        return None

    target = urlunsplit(
        (
            request.scheme,
            google_host,
            request.full_path.rstrip("?"),
            "",
            "",
        )
    )
    return target


def _generate_otp():
    return f"{secrets.randbelow(1000000):06d}"


def _client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.remote_addr or ""


def _request_path():
    return request.full_path.rstrip("?") or request.path


def _request_listing_id():
    if request.view_args and request.view_args.get("listing_id") is not None:
        return request.view_args.get("listing_id")
    return request.form.get("listing_id", type=int) or request.args.get(
        "listing_id", type=int
    )


def _footprint_payload(user, event_type):
    return {
        "user_id": user.get("id"),
        "event_type": event_type,
        "path": _request_path(),
        "method": request.method,
        "endpoint": request.endpoint or "",
        "listing_id": _request_listing_id(),
        "ip_address": _client_ip(),
        "user_agent": request.headers.get("User-Agent", ""),
        "referrer": request.referrer or "",
    }


def _event_type_for_request():
    event_by_endpoint = {
        "home": "listing_search" if request.query_string else "home_view",
        "property_detail": "property_view",
        "api_listings": "api_listings",
        "api_listing_detail": "api_listing_detail",
        "api_listing_recommendations": "api_listing_recommendations",
        "inquiry": "inquiry_submit",
    }
    return event_by_endpoint.get(request.endpoint, request.endpoint or "activity")


def _start_phone_otp(mode, phone, name="", next_url=None):
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        flash("Enter a valid phone number.", "error")
        return None

    existing_user = find_user_by_phone(normalized_phone)
    if mode == "login" and existing_user is None:
        flash("No account exists for this phone number. Sign up first.", "error")
        return None
    if mode == "signup" and existing_user is not None:
        flash("This phone number already has an account. Log in instead.", "info")
        return None

    if not OTP_DEMO_MODE:
        flash(
            "SMS delivery is not configured. Use Google sign-in or enable OTP_DEMO_MODE for local testing.",
            "error",
        )
        return None

    otp = _generate_otp()
    challenge_id = save_challenge(
        USER_OTP_CHALLENGE,
        {
            "mode": mode,
            "phone": normalized_phone,
            "name": (name or "").strip(),
            "otp_hash": generate_password_hash(otp),
            "next": next_url or url_for("home"),
        },
        OTP_TTL_SECONDS,
    )
    session["pending_otp_id"] = challenge_id

    session["demo_otp"] = otp
    flash(f"Demo OTP: {otp}", "info")

    return redirect(url_for("verify_otp"))


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if _current_user():
            return view(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401

        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    return wrapped_view


@app.before_request
def require_login():
    canonical_url = _canonical_local_url()
    if canonical_url:
        return redirect(canonical_url, code=308)

    if request.path.startswith(("/api/admin/", "/api/operations/")):
        return jsonify({"error": "Not found"}), 404
    if request.path.startswith(("/admin", "/operations")):
        return render_template("404.html"), 404

    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint == "api_storage":
        return None
    if _current_user():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.after_request
def track_user_footprint(response):
    if response.status_code >= 400 or request.endpoint in TRACKING_EXCLUDED_ENDPOINTS:
        return response

    user = _current_user()
    if user:
        record_user_footprint(_footprint_payload(user, _event_type_for_request()))

    return response


@app.context_processor
def inject_auth_context():
    current_user = _current_user()
    return {
        "current_user": current_user,
        "google_oauth_enabled": _google_enabled(),
        "dashboard_url": "",
        "dashboard_label": "",
        "site_home_url": url_for("home"),
        "login_url": url_for("login"),
        "signup_url": url_for("signup"),
        "logout_url": url_for("logout"),
        "storage_api_url": url_for("api_storage"),
    }


@app.route("/login", methods=["GET", "POST"])
@rate_limit("user_login_otp", limit=5, window_seconds=300, include_form=("phone",))
def login():
    if _current_user():
        return redirect(_safe_next())

    next_url = _normalize_next_url(request.values.get("next"))
    if request.method == "POST":
        phone = request.form.get("phone", "")
        otp_redirect = _start_phone_otp("login", phone, next_url=next_url)
        if otp_redirect:
            return otp_redirect

    return render_template("login.html", next_url=next_url)


@app.route("/signup", methods=["GET", "POST"])
@rate_limit("user_signup_otp", limit=5, window_seconds=300, include_form=("phone",))
def signup():
    if _current_user():
        return redirect(_safe_next())

    next_url = _normalize_next_url(request.values.get("next"))
    if request.method == "POST":
        name, name_too_long = _form_text("name", 160)
        phone = request.form.get("phone", "")
        if not name:
            flash("Name is required.", "error")
        elif name_too_long:
            flash("Name is too long.", "error")
        else:
            otp_redirect = _start_phone_otp("signup", phone, name=name, next_url=next_url)
            if otp_redirect:
                return otp_redirect

    return render_template("signup.html", next_url=next_url)


@app.route("/verify-otp", methods=["GET", "POST"])
@rate_limit("user_verify_otp", limit=8, window_seconds=300)
def verify_otp():
    pending_otp_id = session.get("pending_otp_id")
    pending_otp = load_challenge(pending_otp_id, USER_OTP_CHALLENGE)
    if not pending_otp:
        session.pop("pending_otp_id", None)
        session.pop("demo_otp", None)
        flash("Start with login or signup to receive an OTP.", "info")
        return redirect(url_for("login"))

    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        if time.time() > pending_otp.get("expires_at", 0):
            delete_challenge(pending_otp_id)
            session.pop("pending_otp_id", None)
            session.pop("demo_otp", None)
            flash("OTP expired. Request a new code.", "error")
            return redirect(url_for("login"))

        if not check_password_hash(pending_otp.get("otp_hash", ""), code):
            flash("Invalid OTP. Try again.", "error")
            return render_template(
                "verify_otp.html",
                pending_otp=pending_otp,
                demo_otp=session.get("demo_otp"),
            )

        if pending_otp.get("mode") == "signup":
            user = upsert_phone_user(pending_otp.get("name", ""), pending_otp["phone"])
        else:
            user = find_user_by_phone(pending_otp["phone"])
            if user is None:
                flash("Account not found. Sign up first.", "error")
                return redirect(url_for("signup"))
            user = upsert_phone_user(user.get("name", ""), pending_otp["phone"])

        next_url = pending_otp.get("next") or url_for("home")
        delete_challenge(pending_otp_id)
        session.pop("pending_otp_id", None)
        session.pop("demo_otp", None)
        _login_user(user)
        flash("You are signed in.", "success")
        return redirect(next_url)

    return render_template(
        "verify_otp.html",
        pending_otp=pending_otp,
        demo_otp=session.get("demo_otp"),
    )


@app.get("/auth/google")
def google_login():
    if not _google_enabled():
        flash("Google sign-in needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.", "error")
        return redirect(url_for("login"))

    session["oauth_next"] = _normalize_next_url(request.args.get("next"))
    return google_oauth.authorize_redirect(_google_redirect_uri())


@app.get("/auth/google/callback")
def google_callback():
    if not _google_enabled():
        flash("Google sign-in is not configured.", "error")
        return redirect(url_for("login"))

    try:
        token = google_oauth.authorize_access_token()
        userinfo = token.get("userinfo")
        if userinfo is None:
            userinfo = google_oauth.get("userinfo").json()
    except Exception as error:
        app.logger.exception("Google sign-in failed")
        error_message = str(error).strip()
        if error_message:
            flash(f"Google sign-in failed: {type(error).__name__}: {error_message}", "error")
        else:
            flash(f"Google sign-in failed: {type(error).__name__}", "error")
        return redirect(url_for("login"))

    google_sub = userinfo.get("sub")
    email = userinfo.get("email", "")
    if not google_sub and not email:
        flash("Google did not return a usable profile.", "error")
        return redirect(url_for("login"))

    next_url = _safe_next()
    user = upsert_google_user(
        google_sub=google_sub,
        email=email,
        name=userinfo.get("name") or email,
        avatar_url=userinfo.get("picture", ""),
    )
    _login_user(user)
    flash("You are signed in with Google.", "success")
    return redirect(next_url)


@app.get("/logout")
def logout():
    session.clear()
    flash("You are signed out.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    filters = _filters_from_request()
    return render_template("index.html", filters=filters)


@app.route("/property/<int:listing_id>")
@login_required
def property_detail(listing_id):
    return render_template("property.html", listing_id=listing_id)


@app.get("/api/storage")
@rate_limit("api_storage", limit=120, window_seconds=60, methods=("GET",))
def api_storage():
    return jsonify({"storage_label": storage_label()})


@app.get("/api/listings")
@login_required
@rate_limit("api_listings", limit=120, window_seconds=60, methods=("GET",))
def api_listings():
    filters = _filters_from_request()
    listings = search_listings(filters)
    return jsonify({"count": len(listings), "filters": filters, "listings": listings})


@app.get("/api/listings/<int:listing_id>")
@login_required
@rate_limit("api_listing_detail", limit=120, window_seconds=60, methods=("GET",))
def api_listing_detail(listing_id):
    listing = get_listing(listing_id)
    if listing is None:
        return jsonify({"error": "Listing not found"}), 404
    return jsonify({"listing": listing})


@app.get("/api/listings/<int:listing_id>/recommendations")
@login_required
@rate_limit("api_listing_recommendations", limit=120, window_seconds=60, methods=("GET",))
def api_listing_recommendations(listing_id):
    listing = get_listing(listing_id)
    if listing is None:
        return jsonify({"error": "Listing not found"}), 404

    limit = request.args.get("limit", 3, type=int)
    return jsonify(recommended_listings(listing_id=listing_id, limit=limit))


@app.route("/inquiry", methods=["POST"])
@login_required
@rate_limit("inquiry_submit", limit=5, window_seconds=300)
def inquiry():
    listing_id = request.form.get("listing_id", type=int)
    name, name_too_long = _form_text("name", 160)
    email, email_too_long = _form_text("email", 254)
    phone, phone_too_long = _form_text("phone", 40)
    message, message_too_long = _form_text("message", 2000)

    if not listing_id or not name or not email:
        flash("Name, email, and listing are required.", "error")
        return redirect(request.referrer or url_for("home"))
    if name_too_long or email_too_long or phone_too_long or message_too_long:
        flash("One or more fields are too long.", "error")
        return redirect(request.referrer or url_for("home"))
    if not _email_valid(email):
        flash("Enter a valid email address.", "error")
        return redirect(request.referrer or url_for("home"))

    saved = create_inquiry(
        {
            "listing_id": listing_id,
            "name": name,
            "email": email,
            "phone": phone,
            "message": message,
        }
    )

    if saved:
        flash("Your inquiry has been sent. The owner will contact you soon.", "success")
    else:
        flash("Inquiry received in demo mode. Configure MySQL to save it permanently.", "info")

    return redirect(url_for("property_detail", listing_id=listing_id))


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=_env_int("APP_PORT", 5000),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
