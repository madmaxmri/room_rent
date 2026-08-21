import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from db import (
    SQL_UNAVAILABLE,
    demo_fallback_enabled,
    find_sql_user_by_email,
    find_sql_user_by_google_sub,
    find_sql_user_by_id,
    find_sql_user_by_phone,
    list_sql_users,
    upsert_sql_google_user,
    upsert_sql_phone_user,
)


DATA_DIR = Path(__file__).resolve().parent / "instance"
USERS_PATH = DATA_DIR / "users.json"


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_users():
    if not USERS_PATH.exists():
        return []

    try:
        payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    users = payload.get("users", [])
    if not isinstance(users, list):
        return []
    return users


def _save_users(users):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(
        json.dumps({"users": users}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _sql_or_json(sql_user, json_lookup):
    if sql_user is SQL_UNAVAILABLE:
        if not demo_fallback_enabled():
            raise RuntimeError("MySQL is unavailable and JSON auth fallback is disabled.")
        return json_lookup()
    if sql_user is None:
        return json_lookup()
    return sql_user


def normalize_phone(phone):
    raw_phone = (phone or "").strip()
    if not raw_phone:
        return ""

    cleaned = re.sub(r"[\s().-]", "", raw_phone)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = re.sub(r"\D", "", cleaned[1:])
        return f"+{digits}" if len(digits) >= 8 else ""

    digits = re.sub(r"\D", "", cleaned)
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) >= 8:
        return f"+{digits}"
    return ""


def _initials(name, fallback):
    parts = [part for part in (name or "").strip().split() if part]
    if len(parts) >= 2:
        return f"{parts[0][0]}{parts[-1][0]}".upper()
    if len(parts) == 1:
        return parts[0][:2].upper()

    fallback_text = (fallback or "U").strip()
    return fallback_text[:2].upper() or "U"


def public_user(user):
    if not user:
        return None

    display_name = user.get("name") or user.get("email") or user.get("phone") or "User"
    auth_method = user.get("auth_method") or user.get("provider") or "phone"
    auth_label = "Google account" if auth_method == "google" else "Phone OTP"

    return {
        "id": user.get("id"),
        "name": display_name,
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "avatar_url": user.get("avatar_url") or "",
        "auth_method": auth_method,
        "auth_label": auth_label,
        "initials": _initials(display_name, user.get("phone") or user.get("email")),
        "verified": bool(user.get("verified")),
        "created_at": user.get("created_at") or "",
        "last_login_at": user.get("last_login_at") or "",
    }


def find_user_by_id(user_id):
    return _sql_or_json(
        find_sql_user_by_id(user_id),
        lambda: next((user for user in _load_users() if user.get("id") == user_id), None),
    )


def find_user_by_phone(phone):
    normalized_phone = normalize_phone(phone)
    return _sql_or_json(
        find_sql_user_by_phone(normalized_phone),
        lambda: next(
            (user for user in _load_users() if user.get("phone") == normalized_phone),
            None,
        ),
    )


def find_user_by_email(email):
    normalized_email = (email or "").strip().lower()
    return _sql_or_json(
        find_sql_user_by_email(normalized_email),
        lambda: next(
            (user for user in _load_users() if user.get("email") == normalized_email),
            None,
        ),
    )


def find_user_by_google_sub(google_sub):
    return _sql_or_json(
        find_sql_user_by_google_sub(google_sub),
        lambda: next(
            (user for user in _load_users() if user.get("google_sub") == google_sub),
            None,
        ),
    )


def list_users():
    sql_users = list_sql_users()
    if sql_users is SQL_UNAVAILABLE:
        if not demo_fallback_enabled():
            raise RuntimeError("MySQL is unavailable and JSON auth fallback is disabled.")
        return _load_users()
    return sql_users


def upsert_phone_user(name, phone):
    normalized_phone = normalize_phone(phone)
    sql_user = upsert_sql_phone_user(name, normalized_phone)
    if sql_user is not SQL_UNAVAILABLE:
        return sql_user
    if not demo_fallback_enabled():
        raise RuntimeError("MySQL is unavailable and JSON auth fallback is disabled.")

    users = _load_users()
    now = _now_iso()

    for user in users:
        if user.get("phone") == normalized_phone:
            if name:
                user["name"] = name.strip()
            user["auth_method"] = "phone"
            user["verified"] = True
            user["last_login_at"] = now
            _save_users(users)
            return user

    user = {
        "id": uuid.uuid4().hex,
        "name": name.strip() if name else normalized_phone,
        "email": "",
        "phone": normalized_phone,
        "avatar_url": "",
        "auth_method": "phone",
        "verified": True,
        "created_at": now,
        "last_login_at": now,
    }
    users.append(user)
    _save_users(users)
    return user


def upsert_google_user(google_sub, email, name, avatar_url):
    normalized_email = (email or "").strip().lower()
    sql_user = upsert_sql_google_user(
        google_sub=google_sub,
        email=normalized_email,
        name=name,
        avatar_url=avatar_url,
    )
    if sql_user is not SQL_UNAVAILABLE:
        return sql_user
    if not demo_fallback_enabled():
        raise RuntimeError("MySQL is unavailable and JSON auth fallback is disabled.")

    users = _load_users()
    now = _now_iso()

    existing_user = None
    if google_sub:
        existing_user = next(
            (user for user in users if user.get("google_sub") == google_sub),
            None,
        )
    if existing_user is None and normalized_email:
        existing_user = next(
            (user for user in users if user.get("email") == normalized_email),
            None,
        )

    if existing_user:
        existing_user["google_sub"] = google_sub or existing_user.get("google_sub", "")
        existing_user["email"] = normalized_email or existing_user.get("email", "")
        existing_user["name"] = name or existing_user.get("name") or normalized_email
        existing_user["avatar_url"] = avatar_url or existing_user.get("avatar_url", "")
        existing_user["auth_method"] = "google"
        existing_user["verified"] = True
        existing_user["last_login_at"] = now
        _save_users(users)
        return existing_user

    user = {
        "id": uuid.uuid4().hex,
        "google_sub": google_sub or "",
        "name": name or normalized_email or "Google user",
        "email": normalized_email,
        "phone": "",
        "avatar_url": avatar_url or "",
        "auth_method": "google",
        "verified": True,
        "created_at": now,
        "last_login_at": now,
    }
    users.append(user)
    _save_users(users)
    return user
