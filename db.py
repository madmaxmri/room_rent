import json
import os
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except Exception:  # pragma: no cover - keeps the demo usable without MySQL packages.
    mysql = None
    MySQLError = Exception


SAMPLE_LISTINGS = [
    {
        "id": 1,
        "title": "Sunlit 2BHK Near Metro",
        "city": "Bengaluru",
        "neighborhood": "Indiranagar",
        "address": "12 8th Main Road, Indiranagar",
        "monthly_rent": 32000,
        "deposit": 96000,
        "bedrooms": 2,
        "bathrooms": 2,
        "area_sqft": 980,
        "property_type": "Apartment",
        "furnished_status": "Semi furnished",
        "image_url": "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?auto=format&fit=crop&w=1200&q=80",
        "description": "Bright apartment with a practical layout, balcony, parking, and fast access to metro, cafes, and grocery stores.",
        "amenities": "Balcony, Covered parking, Lift, Security, Power backup",
        "owner_name": "Asha Rao",
        "owner_phone": "+91 98765 11001",
        "is_featured": True,
        "is_available": True,
    },
    {
        "id": 2,
        "title": "Compact Studio for Working Professionals",
        "city": "Pune",
        "neighborhood": "Hinjewadi Phase 1",
        "address": "Blue Ridge Township, Hinjewadi",
        "monthly_rent": 18000,
        "deposit": 36000,
        "bedrooms": 1,
        "bathrooms": 1,
        "area_sqft": 480,
        "property_type": "Studio",
        "furnished_status": "Fully furnished",
        "image_url": "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=1200&q=80",
        "description": "Move-in ready studio with work desk, wardrobe, appliances, and a short commute to IT parks.",
        "amenities": "Wi-Fi, Washing machine, Gym access, Security, Maintenance",
        "owner_name": "Rohit Mehta",
        "owner_phone": "+91 98765 11002",
        "is_featured": False,
        "is_available": True,
    },
    {
        "id": 3,
        "title": "Family Villa With Private Garden",
        "city": "Hyderabad",
        "neighborhood": "Gachibowli",
        "address": "Villa 18, Green County",
        "monthly_rent": 62000,
        "deposit": 186000,
        "bedrooms": 3,
        "bathrooms": 3,
        "area_sqft": 2100,
        "property_type": "Villa",
        "furnished_status": "Unfurnished",
        "image_url": "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?auto=format&fit=crop&w=1200&q=80",
        "description": "Spacious villa in a gated community with garden space, servant room, clubhouse access, and two-car parking.",
        "amenities": "Garden, Clubhouse, Two-car parking, Security, Pet friendly",
        "owner_name": "Naveen Kumar",
        "owner_phone": "+91 98765 11003",
        "is_featured": True,
        "is_available": True,
    },
    {
        "id": 4,
        "title": "Affordable Room in Shared Flat",
        "city": "Delhi",
        "neighborhood": "Saket",
        "address": "D Block, Saket",
        "monthly_rent": 12500,
        "deposit": 25000,
        "bedrooms": 1,
        "bathrooms": 1,
        "area_sqft": 260,
        "property_type": "Shared Room",
        "furnished_status": "Fully furnished",
        "image_url": "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?auto=format&fit=crop&w=1200&q=80",
        "description": "Private room in a maintained shared flat with common kitchen, living room, and easy access to Yellow Line metro.",
        "amenities": "Bed, Wardrobe, Shared kitchen, Wi-Fi, Housekeeping",
        "owner_name": "Kavya Singh",
        "owner_phone": "+91 98765 11004",
        "is_featured": False,
        "is_available": True,
    },
]

SQL_UNAVAILABLE = object()

DEMO_INQUIRIES = []
DEMO_FOOTPRINTS = []

LISTING_FIELD_NAMES = (
    "id",
    "title",
    "city",
    "neighborhood",
    "address",
    "monthly_rent",
    "deposit",
    "bedrooms",
    "bathrooms",
    "area_sqft",
    "property_type",
    "furnished_status",
    "image_url",
    "description",
    "amenities",
    "owner_name",
    "owner_phone",
    "is_featured",
    "is_available",
)

LISTING_COLUMNS = ", ".join(LISTING_FIELD_NAMES)

USER_COLUMNS = """
    id, google_sub, name, email, phone, avatar_url, auth_method,
    verified, created_at, last_login_at
"""

ADMIN_COLUMNS = """
    id, admin_id, admin_secret_key, display_name, password_hash,
    mfa_secret, mfa_enabled, is_active, created_at, last_login_at
"""

BOOLEAN_FIELDS = ("is_featured", "is_available")
_SCHEMA_READY = False
_AUTH_SCHEMA_READY = False
_ADMIN_SCHEMA_READY = False
_SQL_UNAVAILABLE_UNTIL = 0


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "production"}


def _production_mode():
    return (
        _env_bool("APP_PRODUCTION")
        or _env_bool("PRODUCTION")
        or os.getenv("APP_ENV", "").strip().lower() == "production"
        or os.getenv("FLASK_ENV", "").strip().lower() == "production"
    )


def demo_fallback_enabled():
    return not _env_bool("PRODUCTION_FAIL_CLOSED", _production_mode())


def _database_unavailable():
    if demo_fallback_enabled():
        return
    raise RuntimeError("MySQL is unavailable and demo fallback is disabled.")


def load_env(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _mysql_configured():
    return bool(os.getenv("MYSQL_USER") or os.getenv("DB_USER") or os.getenv("RENT_USE_MYSQL"))


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _db_config():
    try:
        port = int(os.getenv("MYSQL_PORT", os.getenv("DB_PORT", "3306")))
    except (TypeError, ValueError):
        port = 3306

    return {
        "host": os.getenv("MYSQL_HOST", os.getenv("DB_HOST", "127.0.0.1")),
        "port": port,
        "user": os.getenv("MYSQL_USER", os.getenv("DB_USER", "root")),
        "password": os.getenv("MYSQL_PASSWORD", os.getenv("DB_PASSWORD", "")),
        "database": os.getenv("MYSQL_DATABASE", os.getenv("DB_NAME", "room_rent")),
        "connection_timeout": max(1, _env_int("MYSQL_CONNECT_TIMEOUT_SECONDS", 2)),
    }


def _connect():
    global _SQL_UNAVAILABLE_UNTIL

    if mysql is None or not _mysql_configured():
        return None

    if time.monotonic() < _SQL_UNAVAILABLE_UNTIL:
        return None

    try:
        connection = mysql.connector.connect(**_db_config())
        _SQL_UNAVAILABLE_UNTIL = 0
        return connection
    except MySQLError:
        retry_delay = max(1, _env_int("MYSQL_RETRY_DELAY_SECONDS", 15))
        _SQL_UNAVAILABLE_UNTIL = time.monotonic() + retry_delay
        return None


def _now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _db_datetime(value=None):
    parsed = _parse_datetime(value) if value else _now_utc()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _datetime_to_iso(value):
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else ""


def _truncate(value, limit):
    text = "" if value is None else str(value)
    return text[:limit]


def _auth_label(auth_method):
    return "Google account" if auth_method == "google" else "Phone OTP"


def _int_value(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _create_listing_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INT PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            city VARCHAR(120) NOT NULL,
            neighborhood VARCHAR(160) NOT NULL,
            address VARCHAR(255) NOT NULL,
            monthly_rent INT NOT NULL,
            deposit INT NOT NULL,
            bedrooms INT NOT NULL,
            bathrooms INT NOT NULL,
            area_sqft INT NOT NULL,
            property_type VARCHAR(80) NOT NULL,
            furnished_status VARCHAR(80) NOT NULL,
            image_url TEXT,
            description TEXT,
            amenities TEXT,
            owner_name VARCHAR(160),
            owner_phone VARCHAR(40),
            is_featured BOOLEAN NOT NULL DEFAULT FALSE,
            is_available BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_listings_search (city, property_type, furnished_status),
            INDEX idx_listings_price (monthly_rent),
            INDEX idx_listings_available (is_available)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inquiries (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            listing_id INT NOT NULL,
            name VARCHAR(160) NOT NULL,
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(40),
            message TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_inquiries_listing (listing_id),
            INDEX idx_inquiries_created (created_at)
        )
        """
    )


def _create_auth_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(64) PRIMARY KEY,
            google_sub VARCHAR(128) NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NULL UNIQUE,
            phone VARCHAR(40) NULL UNIQUE,
            avatar_url TEXT,
            auth_method VARCHAR(32) NOT NULL DEFAULT 'phone',
            verified BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL,
            last_login_at DATETIME NULL,
            INDEX idx_users_created (created_at),
            INDEX idx_users_last_login (last_login_at),
            INDEX idx_users_auth_method (auth_method)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_challenges (
            id VARCHAR(96) PRIMARY KEY,
            purpose VARCHAR(64) NOT NULL,
            payload_json TEXT NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            INDEX idx_auth_challenges_purpose (purpose),
            INDEX idx_auth_challenges_expires (expires_at)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_footprints (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            path VARCHAR(255) NOT NULL,
            method VARCHAR(12) NOT NULL,
            endpoint VARCHAR(128),
            listing_id INT NULL,
            ip_address VARCHAR(64),
            user_agent VARCHAR(255),
            referrer VARCHAR(255),
            created_at DATETIME NOT NULL,
            INDEX idx_footprints_user_created (user_id, created_at),
            INDEX idx_footprints_created (created_at),
            INDEX idx_footprints_event (event_type),
            CONSTRAINT fk_footprints_user
                FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE CASCADE
        )
        """
    )


def _create_admin_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_accounts (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            admin_id VARCHAR(120) NOT NULL UNIQUE,
            admin_secret_key VARCHAR(128) NOT NULL UNIQUE,
            display_name VARCHAR(160) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            mfa_secret VARCHAR(80) NULL,
            mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL,
            last_login_at DATETIME NULL,
            INDEX idx_admin_accounts_admin_id (admin_id),
            INDEX idx_admin_accounts_active (is_active)
        )
        """
    )
    _ensure_admin_account_columns(cursor)


def _add_column_if_missing(cursor, table, column_definition):
    try:
        cursor.execute(
            "ALTER TABLE {table} ADD COLUMN {column_definition}".format(
                table=table,
                column_definition=column_definition,
            )
        )
    except MySQLError as error:
        if getattr(error, "errno", None) == 1060 or "Duplicate column" in str(error):
            return
        raise


def _ensure_admin_account_columns(cursor):
    _add_column_if_missing(
        cursor,
        "admin_accounts",
        "mfa_secret VARCHAR(80) NULL",
    )
    _add_column_if_missing(
        cursor,
        "admin_accounts",
        "mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    )


def _seed_listings(cursor):
    cursor.execute("SELECT COUNT(*) FROM listings")
    total = cursor.fetchone()[0]
    if total:
        return

    placeholders = ", ".join(["%s"] * len(LISTING_FIELD_NAMES))
    cursor.executemany(
        """
        INSERT INTO listings ({columns})
        VALUES ({placeholders})
        """.format(columns=LISTING_COLUMNS, placeholders=placeholders),
        [tuple(item[field] for field in LISTING_FIELD_NAMES) for item in SAMPLE_LISTINGS],
    )


def initialize_database():
    global _SCHEMA_READY, _AUTH_SCHEMA_READY, _ADMIN_SCHEMA_READY

    if _SCHEMA_READY:
        return True

    connection = _connect()
    if connection is None:
        _database_unavailable()
        return False

    with closing(connection):
        cursor = connection.cursor()
        try:
            _create_listing_tables(cursor)
            _create_auth_tables(cursor)
            _create_admin_tables(cursor)
            _seed_listings(cursor)
            connection.commit()
            _SCHEMA_READY = True
            _AUTH_SCHEMA_READY = True
            _ADMIN_SCHEMA_READY = True
            return True
        except MySQLError:
            connection.rollback()
            return False
        finally:
            cursor.close()


def _ensure_auth_tables(connection):
    global _AUTH_SCHEMA_READY

    if _AUTH_SCHEMA_READY:
        return True

    cursor = connection.cursor()
    try:
        _create_auth_tables(cursor)
        connection.commit()
        _AUTH_SCHEMA_READY = True
        return True
    except MySQLError:
        connection.rollback()
        return False
    finally:
        cursor.close()


def _ensure_admin_tables(connection):
    global _ADMIN_SCHEMA_READY

    if _ADMIN_SCHEMA_READY:
        return True

    cursor = connection.cursor()
    try:
        _create_admin_tables(cursor)
        connection.commit()
        _ADMIN_SCHEMA_READY = True
        return True
    except MySQLError:
        connection.rollback()
        return False
    finally:
        cursor.close()


def storage_label():
    connection = _connect()
    if connection is None:
        _database_unavailable()
        return "Demo data"
    connection.close()
    return "MySQL"


def create_auth_challenge(purpose, payload, expires_at):
    purpose = _truncate(purpose, 64)
    if not purpose or not isinstance(payload, dict):
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        challenge_id = uuid.uuid4().hex
        cursor = connection.cursor()
        try:
            cursor.execute(
                "DELETE FROM auth_challenges WHERE expires_at < %s",
                (_db_datetime(),),
            )
            cursor.execute(
                """
                INSERT INTO auth_challenges (
                    id, purpose, payload_json, expires_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    challenge_id,
                    purpose,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    _db_datetime(expires_at),
                    _db_datetime(),
                ),
            )
            connection.commit()
            return challenge_id
        except (MySQLError, TypeError, ValueError):
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def get_auth_challenge(challenge_id, purpose=None):
    challenge_id = (challenge_id or "").strip()
    purpose = (purpose or "").strip()
    if not challenge_id:
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            query = """
                SELECT id, purpose, payload_json, expires_at
                FROM auth_challenges
                WHERE id = %s
            """
            params = [challenge_id]
            if purpose:
                query += " AND purpose = %s"
                params.append(purpose)
            query += " LIMIT 1"
            cursor.execute(query, params)
            row = cursor.fetchone()
            if not row:
                return None

            expires_at = _parse_datetime(row.get("expires_at"))
            if not expires_at or expires_at < _now_utc():
                delete_auth_challenge(challenge_id)
                return None

            payload = json.loads(row.get("payload_json") or "{}")
            return payload if isinstance(payload, dict) else None
        except (MySQLError, json.JSONDecodeError, TypeError, ValueError):
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def delete_auth_challenge(challenge_id):
    challenge_id = (challenge_id or "").strip()
    if not challenge_id:
        return False

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor()
        try:
            cursor.execute(
                "DELETE FROM auth_challenges WHERE id = %s",
                (challenge_id,),
            )
            connection.commit()
            return cursor.rowcount > 0
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def _clean_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_listing(row):
    if row is None:
        return None

    listing = dict(row)
    for field in BOOLEAN_FIELDS:
        if field in listing:
            listing[field] = bool(listing[field])
    return listing


def _sample_listing(listing):
    if listing is None:
        return None
    return dict(listing)


def search_listings(filters):
    connection = _connect()
    if connection is None:
        _database_unavailable()
        return _search_sample_listings(filters)

    query = """
        SELECT {columns}
        FROM listings
        WHERE is_available = TRUE
    """.format(columns=LISTING_COLUMNS)
    params = []

    city = filters.get("city")
    property_type = filters.get("property_type")
    furnished_status = filters.get("furnished_status")
    min_price = _clean_int(filters.get("min_price"))
    max_price = _clean_int(filters.get("max_price"))
    bedrooms = _clean_int(filters.get("bedrooms"))

    if city:
        query += " AND (city LIKE %s OR neighborhood LIKE %s)"
        params.extend([f"%{city}%", f"%{city}%"])
    if property_type:
        query += " AND property_type = %s"
        params.append(property_type)
    if furnished_status:
        query += " AND furnished_status = %s"
        params.append(furnished_status)
    if min_price is not None:
        query += " AND monthly_rent >= %s"
        params.append(min_price)
    if max_price is not None:
        query += " AND monthly_rent <= %s"
        params.append(max_price)
    if bedrooms is not None:
        query += " AND bedrooms >= %s"
        params.append(bedrooms)

    query += " ORDER BY is_featured DESC, monthly_rent ASC"

    with closing(connection):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return [_normalize_listing(row) for row in cursor.fetchall()]
        except MySQLError:
            _database_unavailable()
            return _search_sample_listings(filters)
        finally:
            cursor.close()


def get_listing(listing_id):
    connection = _connect()
    if connection is None:
        _database_unavailable()
        return _sample_listing(
            next((item for item in SAMPLE_LISTINGS if item["id"] == listing_id), None)
        )

    with closing(connection):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT {columns}
                FROM listings
                WHERE id = %s AND is_available = TRUE
                """.format(columns=LISTING_COLUMNS),
                (listing_id,),
            )
            return _normalize_listing(cursor.fetchone())
        except MySQLError:
            _database_unavailable()
            return _sample_listing(
                next((item for item in SAMPLE_LISTINGS if item["id"] == listing_id), None)
            )
        finally:
            cursor.close()


def list_all_listings(include_unavailable=True):
    connection = _connect()
    if connection is None:
        _database_unavailable()
        listings = SAMPLE_LISTINGS
        if not include_unavailable:
            listings = [item for item in listings if item["is_available"]]
        return [_sample_listing(item) for item in listings]

    query = """
        SELECT {columns}
        FROM listings
    """.format(columns=LISTING_COLUMNS)
    params = []
    if not include_unavailable:
        query += " WHERE is_available = TRUE"
    query += " ORDER BY id ASC"

    with closing(connection):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return [_normalize_listing(row) for row in cursor.fetchall()]
        except MySQLError:
            _database_unavailable()
            listings = SAMPLE_LISTINGS
            if not include_unavailable:
                listings = [item for item in listings if item["is_available"]]
            return [_sample_listing(item) for item in listings]
        finally:
            cursor.close()


def create_inquiry(inquiry):
    connection = _connect()
    if connection is None:
        _database_unavailable()
        DEMO_INQUIRIES.append(dict(inquiry))
        return False

    with closing(connection):
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO inquiries (listing_id, name, email, phone, message)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    inquiry["listing_id"],
                    inquiry["name"],
                    inquiry["email"],
                    inquiry["phone"],
                    inquiry["message"],
                ),
            )
            connection.commit()
            return True
        except MySQLError:
            connection.rollback()
            return False
        finally:
            cursor.close()


def _normalize_user(row):
    if row is None:
        return None

    user = dict(row)
    for field in ("google_sub", "email", "phone", "avatar_url"):
        user[field] = user.get(field) or ""
    user["verified"] = bool(user.get("verified"))
    user["created_at"] = _datetime_to_iso(user.get("created_at"))
    user["last_login_at"] = _datetime_to_iso(user.get("last_login_at"))
    return user


def _fetch_user_with_cursor(cursor, column, value):
    cursor.execute(
        """
        SELECT {columns}
        FROM users
        WHERE {column} = %s
        LIMIT 1
        """.format(columns=USER_COLUMNS, column=column),
        (value,),
    )
    return _normalize_user(cursor.fetchone())


def _find_sql_user(column, value):
    if not value:
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            return _fetch_user_with_cursor(cursor, column, value)
        except MySQLError:
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def find_sql_user_by_id(user_id):
    return _find_sql_user("id", user_id)


def find_sql_user_by_phone(phone):
    return _find_sql_user("phone", phone)


def find_sql_user_by_email(email):
    normalized_email = (email or "").strip().lower()
    return _find_sql_user("email", normalized_email)


def find_sql_user_by_google_sub(google_sub):
    return _find_sql_user("google_sub", google_sub)


def list_sql_users():
    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT {columns}
                FROM users
                ORDER BY created_at DESC
                """.format(columns=USER_COLUMNS)
            )
            return [_normalize_user(row) for row in cursor.fetchall()]
        except MySQLError:
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def upsert_sql_phone_user(name, phone):
    normalized_phone = (phone or "").strip()
    if not normalized_phone:
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            existing_user = _fetch_user_with_cursor(cursor, "phone", normalized_phone)
            now = _db_datetime()

            if existing_user:
                display_name = (name or "").strip() or existing_user.get("name") or normalized_phone
                cursor.execute(
                    """
                    UPDATE users
                    SET name = %s,
                        auth_method = 'phone',
                        verified = TRUE,
                        last_login_at = %s
                    WHERE id = %s
                    """,
                    (display_name, now, existing_user["id"]),
                )
                user_id = existing_user["id"]
            else:
                user_id = uuid.uuid4().hex
                display_name = (name or "").strip() or normalized_phone
                cursor.execute(
                    """
                    INSERT INTO users (
                        id, name, email, phone, avatar_url, auth_method,
                        verified, created_at, last_login_at
                    )
                    VALUES (%s, %s, NULL, %s, NULL, 'phone', TRUE, %s, %s)
                    """,
                    (user_id, display_name, normalized_phone, now, now),
                )

            connection.commit()
            return _fetch_user_with_cursor(cursor, "id", user_id)
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def upsert_sql_google_user(google_sub, email, name, avatar_url):
    google_sub_value = (google_sub or "").strip() or None
    normalized_email = (email or "").strip().lower() or None

    if not google_sub_value and not normalized_email:
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_auth_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            existing_user = None
            if google_sub_value:
                existing_user = _fetch_user_with_cursor(
                    cursor, "google_sub", google_sub_value
                )
            if existing_user is None and normalized_email:
                existing_user = _fetch_user_with_cursor(
                    cursor, "email", normalized_email
                )

            now = _db_datetime()
            if existing_user:
                user_id = existing_user["id"]
                next_google_sub = google_sub_value or existing_user.get("google_sub") or None
                next_email = normalized_email or existing_user.get("email") or None
                display_name = (name or "").strip() or existing_user.get("name") or next_email or "Google user"
                next_avatar_url = (avatar_url or "").strip() or existing_user.get("avatar_url") or None
                cursor.execute(
                    """
                    UPDATE users
                    SET google_sub = %s,
                        email = %s,
                        name = %s,
                        avatar_url = %s,
                        auth_method = 'google',
                        verified = TRUE,
                        last_login_at = %s
                    WHERE id = %s
                    """,
                    (
                        next_google_sub,
                        next_email,
                        display_name,
                        next_avatar_url,
                        now,
                        user_id,
                    ),
                )
            else:
                user_id = uuid.uuid4().hex
                display_name = (name or "").strip() or normalized_email or "Google user"
                cursor.execute(
                    """
                    INSERT INTO users (
                        id, google_sub, name, email, phone, avatar_url,
                        auth_method, verified, created_at, last_login_at
                    )
                    VALUES (%s, %s, %s, %s, NULL, %s, 'google', TRUE, %s, %s)
                    """,
                    (
                        user_id,
                        google_sub_value,
                        display_name,
                        normalized_email,
                        (avatar_url or "").strip() or None,
                        now,
                        now,
                    ),
                )

            connection.commit()
            return _fetch_user_with_cursor(cursor, "id", user_id)
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def _normalize_admin(row):
    if row is None:
        return None

    admin = dict(row)
    admin["id"] = _int_value(admin.get("id"))
    admin["mfa_secret"] = admin.get("mfa_secret") or ""
    admin["mfa_enabled"] = bool(admin.get("mfa_enabled"))
    admin["is_active"] = bool(admin.get("is_active"))
    admin["created_at"] = _datetime_to_iso(admin.get("created_at"))
    admin["last_login_at"] = _datetime_to_iso(admin.get("last_login_at"))
    return admin


def _fetch_admin_with_cursor(cursor, column, value):
    cursor.execute(
        """
        SELECT {columns}
        FROM admin_accounts
        WHERE {column} = %s
        LIMIT 1
        """.format(columns=ADMIN_COLUMNS, column=column),
        (value,),
    )
    return _normalize_admin(cursor.fetchone())


def _find_admin(column, value):
    if value in (None, ""):
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_admin_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            return _fetch_admin_with_cursor(cursor, column, value)
        except MySQLError:
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def find_admin_account_by_record_id(record_id):
    return _find_admin("id", record_id)


def find_admin_account_by_admin_id(admin_id):
    return _find_admin("admin_id", (admin_id or "").strip())


def upsert_admin_account(admin_id, password_hash, admin_secret_key=None, display_name="Admin"):
    normalized_admin_id = (admin_id or "").strip()
    requested_secret_key = (admin_secret_key or "").strip()
    normalized_display_name = (display_name or "").strip() or normalized_admin_id

    if not normalized_admin_id or not password_hash:
        return None

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_admin_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor(dictionary=True)
        try:
            existing_admin = _fetch_admin_with_cursor(
                cursor, "admin_id", normalized_admin_id
            )
            now = _db_datetime()
            normalized_secret_key = (
                requested_secret_key
                or (existing_admin or {}).get("admin_secret_key")
                or uuid.uuid4().hex
            )

            if existing_admin:
                cursor.execute(
                    """
                    UPDATE admin_accounts
                    SET admin_secret_key = %s,
                        display_name = %s,
                        password_hash = %s,
                        is_active = TRUE
                    WHERE id = %s
                    """,
                    (
                        normalized_secret_key,
                        normalized_display_name,
                        password_hash,
                        existing_admin["id"],
                    ),
                )
                admin_record_id = existing_admin["id"]
            else:
                cursor.execute(
                    """
                    INSERT INTO admin_accounts (
                        admin_id, admin_secret_key, display_name, password_hash,
                        is_active, created_at, last_login_at
                    )
                    VALUES (%s, %s, %s, %s, TRUE, %s, NULL)
                    """,
                    (
                        normalized_admin_id,
                        normalized_secret_key,
                        normalized_display_name,
                        password_hash,
                        now,
                    ),
                )
                admin_record_id = cursor.lastrowid

            connection.commit()
            return _fetch_admin_with_cursor(cursor, "id", admin_record_id)
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def mark_admin_login(admin_record_id):
    connection = _connect()
    if connection is None:
        return False

    with closing(connection):
        if not _ensure_admin_tables(connection):
            return False

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE admin_accounts
                SET last_login_at = %s
                WHERE id = %s
                """,
                (_db_datetime(), admin_record_id),
            )
            connection.commit()
            return True
        except MySQLError:
            connection.rollback()
            return False
        finally:
            cursor.close()


def update_admin_password(admin_record_id, password_hash):
    if not admin_record_id or not password_hash:
        return False

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_admin_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE admin_accounts
                SET password_hash = %s
                WHERE id = %s
                """,
                (password_hash, admin_record_id),
            )
            connection.commit()
            return cursor.rowcount > 0
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def update_admin_mfa(admin_record_id, mfa_secret=None, mfa_enabled=False):
    if not admin_record_id:
        return False

    connection = _connect()
    if connection is None:
        return SQL_UNAVAILABLE

    with closing(connection):
        if not _ensure_admin_tables(connection):
            return SQL_UNAVAILABLE

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE admin_accounts
                SET mfa_secret = %s,
                    mfa_enabled = %s
                WHERE id = %s
                """,
                (
                    (mfa_secret or "").strip() or None,
                    bool(mfa_enabled),
                    admin_record_id,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0
        except MySQLError:
            connection.rollback()
            return SQL_UNAVAILABLE
        finally:
            cursor.close()


def _operation_user(row):
    user = _normalize_user(row)
    if user is None:
        return None

    auth_method = user.get("auth_method") or "phone"
    return {
        "id": user.get("id"),
        "name": user.get("name") or user.get("email") or user.get("phone") or "User",
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "auth_method": auth_method,
        "auth_label": _auth_label(auth_method),
        "verified": bool(user.get("verified")),
        "created_at": user.get("created_at") or "",
        "last_login_at": user.get("last_login_at") or "",
        "footprint_count": _int_value(user.get("footprint_count")),
        "last_activity_at": _datetime_to_iso(user.get("last_activity_at")),
        "last_path": user.get("last_path") or "",
        "last_event_type": user.get("last_event_type") or "",
    }


def _normalize_new_footprint(footprint):
    created_at = _datetime_to_iso(footprint.get("created_at")) or _now_utc().isoformat()
    return {
        "user_id": _truncate(footprint.get("user_id"), 64),
        "event_type": _truncate(footprint.get("event_type") or "activity", 64),
        "path": _truncate(footprint.get("path") or "/", 255),
        "method": _truncate(footprint.get("method") or "GET", 12),
        "endpoint": _truncate(footprint.get("endpoint"), 128),
        "listing_id": _clean_int(footprint.get("listing_id")),
        "ip_address": _truncate(footprint.get("ip_address"), 64),
        "user_agent": _truncate(footprint.get("user_agent"), 255),
        "referrer": _truncate(footprint.get("referrer"), 255),
        "created_at": created_at,
    }


def record_user_footprint(footprint):
    normalized = _normalize_new_footprint(footprint)
    if not normalized["user_id"]:
        return False

    connection = _connect()
    if connection is None:
        _database_unavailable()
        _record_demo_footprint(normalized)
        return False

    with closing(connection):
        if not _ensure_auth_tables(connection):
            _database_unavailable()
            _record_demo_footprint(normalized)
            return False

        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO user_footprints (
                    user_id, event_type, path, method, endpoint, listing_id,
                    ip_address, user_agent, referrer, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    normalized["user_id"],
                    normalized["event_type"],
                    normalized["path"],
                    normalized["method"],
                    normalized["endpoint"] or None,
                    normalized["listing_id"],
                    normalized["ip_address"] or None,
                    normalized["user_agent"] or None,
                    normalized["referrer"] or None,
                    _db_datetime(normalized["created_at"]),
                ),
            )
            retention_days = _env_int("FOOTPRINT_RETENTION_DAYS", 365)
            if retention_days > 0:
                cutoff = _now_utc() - timedelta(days=retention_days)
                cursor.execute(
                    "DELETE FROM user_footprints WHERE created_at < %s",
                    (_db_datetime(cutoff),),
                )
            connection.commit()
            return True
        except MySQLError:
            connection.rollback()
            _database_unavailable()
            _record_demo_footprint(normalized)
            return False
        finally:
            cursor.close()


def _record_demo_footprint(footprint):
    demo_footprint = dict(footprint)
    demo_footprint["id"] = len(DEMO_FOOTPRINTS) + 1
    DEMO_FOOTPRINTS.append(demo_footprint)
    del DEMO_FOOTPRINTS[:-500]


def _normalize_footprint(row):
    footprint = dict(row)
    auth_method = footprint.get("auth_method") or "phone"
    user_name = (
        footprint.get("name")
        or footprint.get("email")
        or footprint.get("phone")
        or "Unknown user"
    )
    return {
        "id": _int_value(footprint.get("id")),
        "user_id": footprint.get("user_id") or "",
        "user_name": user_name,
        "user_contact": footprint.get("email") or footprint.get("phone") or "",
        "auth_method": auth_method,
        "auth_label": _auth_label(auth_method),
        "event_type": footprint.get("event_type") or "activity",
        "path": footprint.get("path") or "",
        "method": footprint.get("method") or "",
        "endpoint": footprint.get("endpoint") or "",
        "listing_id": footprint.get("listing_id"),
        "ip_address": footprint.get("ip_address") or "",
        "user_agent": footprint.get("user_agent") or "",
        "referrer": footprint.get("referrer") or "",
        "created_at": _datetime_to_iso(footprint.get("created_at")),
    }


def get_operations_dashboard(fallback_users=None, limit=25):
    limit = max(1, min(_clean_int(limit) or 25, 100))
    connection = _connect()
    if connection is None:
        _database_unavailable()
        return _fallback_operations_dashboard(fallback_users or [], limit)

    with closing(connection):
        if not _ensure_auth_tables(connection):
            _database_unavailable()
            return _fallback_operations_dashboard(fallback_users or [], limit)

        cursor = connection.cursor(dictionary=True)
        try:
            return _sql_operations_dashboard(cursor, limit)
        except MySQLError:
            _database_unavailable()
            return _fallback_operations_dashboard(fallback_users or [], limit)
        finally:
            cursor.close()


def _sql_operations_dashboard(cursor, limit):
    now = _now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_users,
            COALESCE(SUM(created_at >= %s), 0) AS new_today,
            COALESCE(SUM(created_at >= %s), 0) AS new_last_7_days,
            COALESCE(SUM(last_login_at >= %s), 0) AS active_last_7_days,
            (SELECT COUNT(*) FROM user_footprints) AS tracked_footprints
        FROM users
        """,
        (_db_datetime(today_start), _db_datetime(week_start), _db_datetime(week_start)),
    )
    stats = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT
            u.id, u.google_sub, u.name, u.email, u.phone, u.avatar_url,
            u.auth_method, u.verified, u.created_at, u.last_login_at,
            COUNT(f.id) AS footprint_count,
            MAX(f.created_at) AS last_activity_at,
            (
                SELECT f2.path
                FROM user_footprints f2
                WHERE f2.user_id = u.id
                ORDER BY f2.created_at DESC, f2.id DESC
                LIMIT 1
            ) AS last_path,
            (
                SELECT f2.event_type
                FROM user_footprints f2
                WHERE f2.user_id = u.id
                ORDER BY f2.created_at DESC, f2.id DESC
                LIMIT 1
            ) AS last_event_type
        FROM users u
        LEFT JOIN user_footprints f ON f.user_id = u.id
        GROUP BY
            u.id, u.google_sub, u.name, u.email, u.phone, u.avatar_url,
            u.auth_method, u.verified, u.created_at, u.last_login_at
        ORDER BY u.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    recent_users = [_operation_user(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT
            f.id, f.user_id, f.event_type, f.path, f.method, f.endpoint,
            f.listing_id, f.ip_address, f.user_agent, f.referrer, f.created_at,
            u.name, u.email, u.phone, u.auth_method
        FROM user_footprints f
        LEFT JOIN users u ON u.id = f.user_id
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    recent_footprints = [_normalize_footprint(row) for row in cursor.fetchall()]

    return {
        "generated_at": _now_utc().isoformat(),
        "storage_label": "MySQL",
        "stats": {
            "total_users": _int_value(stats.get("total_users")),
            "new_today": _int_value(stats.get("new_today")),
            "new_last_7_days": _int_value(stats.get("new_last_7_days")),
            "active_last_7_days": _int_value(stats.get("active_last_7_days")),
            "tracked_footprints": _int_value(stats.get("tracked_footprints")),
        },
        "recent_users": [user for user in recent_users if user],
        "recent_footprints": recent_footprints,
    }


def _fallback_operations_dashboard(users, limit):
    user_map = {user.get("id"): dict(user) for user in users if user.get("id")}
    footprints = [
        _fallback_footprint(footprint, user_map)
        for footprint in sorted(
            DEMO_FOOTPRINTS,
            key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    ]
    footprint_counts = {}
    last_footprint = {}
    for footprint in footprints:
        user_id = footprint.get("user_id")
        footprint_counts[user_id] = footprint_counts.get(user_id, 0) + 1
        last_footprint.setdefault(user_id, footprint)

    recent_users = []
    for user in users:
        operation_user = _operation_user(user)
        if not operation_user:
            continue
        user_id = operation_user["id"]
        latest = last_footprint.get(user_id, {})
        operation_user["footprint_count"] = footprint_counts.get(user_id, 0)
        operation_user["last_activity_at"] = latest.get("created_at", "")
        operation_user["last_path"] = latest.get("path", "")
        operation_user["last_event_type"] = latest.get("event_type", "")
        recent_users.append(operation_user)

    recent_users.sort(
        key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    now = _now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    return {
        "generated_at": now.isoformat(),
        "storage_label": "Demo data",
        "stats": {
            "total_users": len(recent_users),
            "new_today": sum(
                1
                for user in recent_users
                if (_parse_datetime(user.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= today_start
            ),
            "new_last_7_days": sum(
                1
                for user in recent_users
                if (_parse_datetime(user.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= week_start
            ),
            "active_last_7_days": sum(
                1
                for user in recent_users
                if (_parse_datetime(user.get("last_login_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= week_start
            ),
            "tracked_footprints": len(footprints),
        },
        "recent_users": recent_users[:limit],
        "recent_footprints": footprints[:limit],
    }


def _fallback_footprint(footprint, user_map):
    user = user_map.get(footprint.get("user_id"), {})
    row = dict(footprint)
    row["name"] = user.get("name")
    row["email"] = user.get("email")
    row["phone"] = user.get("phone")
    row["auth_method"] = user.get("auth_method")
    return _normalize_footprint(row)


def list_recent_footprints(limit=500):
    limit = max(1, min(_clean_int(limit) or 500, 2000))
    connection = _connect()
    if connection is None:
        _database_unavailable()
        return [
            dict(footprint)
            for footprint in sorted(
                DEMO_FOOTPRINTS,
                key=lambda item: _parse_datetime(item.get("created_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )[:limit]
        ]

    with closing(connection):
        if not _ensure_auth_tables(connection):
            _database_unavailable()
            return []

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT
                    id, user_id, event_type, path, method, endpoint,
                    listing_id, ip_address, user_agent, referrer, created_at
                FROM user_footprints
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [
                {
                    **dict(row),
                    "created_at": _datetime_to_iso(row.get("created_at")),
                }
                for row in cursor.fetchall()
            ]
        except MySQLError:
            _database_unavailable()
            return []
        finally:
            cursor.close()


def _search_sample_listings(filters):
    results = [item for item in SAMPLE_LISTINGS if item["is_available"]]
    city = (filters.get("city") or "").lower()
    property_type = filters.get("property_type") or ""
    furnished_status = filters.get("furnished_status") or ""
    min_price = _clean_int(filters.get("min_price"))
    max_price = _clean_int(filters.get("max_price"))
    bedrooms = _clean_int(filters.get("bedrooms"))

    if city:
        results = [
            item
            for item in results
            if city in item["city"].lower() or city in item["neighborhood"].lower()
        ]
    if property_type:
        results = [item for item in results if item["property_type"] == property_type]
    if furnished_status:
        results = [
            item for item in results if item["furnished_status"] == furnished_status
        ]
    if min_price is not None:
        results = [item for item in results if item["monthly_rent"] >= min_price]
    if max_price is not None:
        results = [item for item in results if item["monthly_rent"] <= max_price]
    if bedrooms is not None:
        results = [item for item in results if item["bedrooms"] >= bedrooms]

    return [
        _sample_listing(item)
        for item in sorted(
            results, key=lambda item: (not item["is_featured"], item["monthly_rent"])
        )
    ]
