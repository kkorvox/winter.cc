"""Self-service account creation for winter.cc.

Accounts created here are NON-ADMIN and receive NO LICENSE. Only existing
owner/admin endpoints may issue a license. Users must obtain their own
license from the owner before logging in.

Add this module to app/main.py immediately after `app = FastAPI(...)`:
    from .registration import router as registration_router
    app.include_router(registration_router)
"""

import hashlib
import ipaddress
import os
import re
import time

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.errors import UniqueViolation

from .core import db, now

router = APIRouter(prefix="/api", tags=["registration"])

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,32}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$")


class Registration(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


def validate_registration(username: str, email: str, password: str):
    username = username.strip()
    email = email.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(400, "Username must be 3–32 letters, numbers, or underscores")
    if not EMAIL_PATTERN.fullmatch(email) or ".." in email or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address")
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise HTTPException(400, "Password must be at least 12 characters and at most 72 bytes")
    return username, email


def request_identity(request: Request) -> str:
    """Use the peer address, not arbitrary client-supplied forwarding headers."""
    address = request.client.host if request.client else "unknown"
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        address = address[:100]
    salt = os.getenv("REGISTRATION_RATE_LIMIT_SALT", "winter.cc registration abuse limit")
    return hashlib.sha256((salt + ":" + address).encode()).hexdigest()


def enforce_registration_limit(conn, identity: str):
    """Fixed-window limit of 10 attempts per 30 minutes, stored in Postgres.

    The rate-limit table only stores a salted hash of the peer IP, not emails.
    Security signals are intentionally not treated as a substitute for CAPTCHA
    or email verification on a high-traffic deployment.
    """
    now_ts = time.time()
    window_end = now_ts + 30 * 60
    conn.execute("DELETE FROM registration_limits WHERE resets_at < %s", (now_ts,))
    row = conn.execute(
        """INSERT INTO registration_limits (ip_hash, attempts, resets_at)
           VALUES (%s, 1, %s)
           ON CONFLICT (ip_hash) DO UPDATE
             SET attempts = CASE
               WHEN registration_limits.resets_at < %s THEN 1
               ELSE registration_limits.attempts + 1 END,
                 resets_at = CASE
               WHEN registration_limits.resets_at < %s THEN %s
               ELSE registration_limits.resets_at END
           RETURNING attempts""",
        (identity, window_end, now_ts, now_ts, window_end),
    ).fetchone()
    if row["attempts"] > 10:
        raise HTTPException(429, "Too many registrations from this connection. Try again later")


def ensure_registration_schema(conn):
    # Safe migration for existing users. Old user accounts keep email = NULL.
    conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_unique
           ON users (LOWER(email)) WHERE email IS NOT NULL"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS registration_limits (
           ip_hash TEXT PRIMARY KEY,
           attempts INTEGER NOT NULL,
           resets_at DOUBLE PRECISION NOT NULL
           )"""
    )


@router.post("/register", status_code=201)
def register(body: Registration, request: Request):
    username, email = validate_registration(body.username, body.email, body.password)
    identity = request_identity(request)

    # Keep rate limit increments even if a later INSERT finds a duplicate.
    with db() as conn:
        ensure_registration_schema(conn)
        enforce_registration_limit(conn, identity)

    hashed = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    try:
        with db() as conn:
            row = conn.execute(
                """INSERT INTO users (username,email,password,is_admin,created_at)
                   VALUES (%s,%s,%s,0,%s) RETURNING id""",
                (username, email, hashed, now()),
            ).fetchone()
            conn.execute(
                """INSERT INTO activity (user_id,action,details,created)
                   VALUES (%s,%s,%s,%s)""",
                (row["id"], "self_registered", "Account created without license", now()),
            )
    except UniqueViolation:
        # Avoid exposing which specific email is registered.
        raise HTTPException(409, "Username or email is already registered")

    return {
        "ok": True,
        "username": username,
        "license_assigned": False,
        "message": "Account created. Ask the winter.cc owner for a license key before signing in.",
    }
