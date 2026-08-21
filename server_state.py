import secrets
import time
from datetime import datetime, timezone

from db import (
    SQL_UNAVAILABLE,
    create_auth_challenge,
    delete_auth_challenge,
    demo_fallback_enabled,
    get_auth_challenge,
)


_MEMORY_CHALLENGES = {}


def _expiry_datetime(expires_at):
    return datetime.fromtimestamp(expires_at, tz=timezone.utc)


def save_challenge(purpose, payload, ttl_seconds):
    expires_at = time.time() + ttl_seconds
    stored_payload = dict(payload)
    stored_payload["expires_at"] = expires_at

    challenge_id = create_auth_challenge(
        purpose,
        stored_payload,
        _expiry_datetime(expires_at),
    )
    if challenge_id is not SQL_UNAVAILABLE:
        return challenge_id

    if not demo_fallback_enabled():
        raise RuntimeError("Database unavailable for auth challenge storage.")

    challenge_id = secrets.token_urlsafe(32)
    _MEMORY_CHALLENGES[challenge_id] = {
        "purpose": purpose,
        "payload": stored_payload,
        "expires_at": expires_at,
    }
    return challenge_id


def load_challenge(challenge_id, purpose):
    if not challenge_id:
        return None

    payload = get_auth_challenge(challenge_id, purpose)
    if payload is not SQL_UNAVAILABLE:
        return payload

    if not demo_fallback_enabled():
        raise RuntimeError("Database unavailable for auth challenge storage.")

    challenge = _MEMORY_CHALLENGES.get(challenge_id)
    if not challenge or challenge.get("purpose") != purpose:
        return None
    if time.time() > challenge.get("expires_at", 0):
        _MEMORY_CHALLENGES.pop(challenge_id, None)
        return None
    return dict(challenge.get("payload") or {})


def consume_challenge(challenge_id, purpose):
    payload = load_challenge(challenge_id, purpose)
    delete_challenge(challenge_id)
    return payload


def delete_challenge(challenge_id):
    if not challenge_id:
        return False

    _MEMORY_CHALLENGES.pop(challenge_id, None)
    deleted = delete_auth_challenge(challenge_id)
    if deleted is SQL_UNAVAILABLE:
        return False
    return deleted
