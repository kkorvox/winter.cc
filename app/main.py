
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from fastapi import (
    FastAPI, HTTPException, Header, Depends,
    Request, Response
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg.errors import UniqueViolation, ForeignKeyViolation

from .core import (
    init, setup_owner, db, auth, current_user,
    candidates, start_search, hash_key, RULES, now,
    create_user, create_license, log_activity
)

init()
setup_owner()

app = FastAPI(
    title="winter.cc private API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

ROOT = Path(__file__).resolve().parent.parent

COOKIE_NAME = "winter_session"
REMEMBER_SECONDS = 30 * 24 * 60 * 60

COOKIE_OPTIONS = {
    "httponly": True,
    "secure": True,
    "samesite": "lax",
    "path": "/api"
}


class Login(BaseModel):
    username: str
    password: str
    license_key: str
    remember: bool = False


class Generate(BaseModel):
    platform: str = "Discord"
    lengths: list[int] = Field(
        default_factory=lambda: [3, 4, 5]
    )
    types: list[str] = Field(
        default_factory=lambda: ["Random letters"]
    )
    characters: list[str] = Field(
        default_factory=lambda: ["Lowercase", "Numbers"]
    )
    prefix: str = ""
    suffix: str = ""
    amount: int = Field(default=100, ge=1, le=100000)
    wordlist: list[str] | None = None


class Check(BaseModel):
    platform: str
    usernames: list[str] = Field(max_length=100000)


class Mark(BaseModel):
    search_id: int
    username: str
    status: str


class Favorite(BaseModel):
    platform: str
    username: str


class Preset(BaseModel):
    name: str
    config: dict


class AdminUser(BaseModel):
    username: str
    password: str


class AdminLicense(BaseModel):
    expires_at: str | None = None


class AdminPassword(BaseModel):
    password: str


class AdminSuspension(BaseModel):
    suspended: bool


def parse_expiration(value):
    if value is None or value == "":
        return None

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            raise ValueError()

        dt = dt.astimezone(timezone.utc)

        if dt <= datetime.now(timezone.utc):
            raise ValueError()

        return dt.isoformat()

    except (ValueError, AttributeError):
        raise HTTPException(
            400,
            "Enter a valid future expiration date"
        )


def get_token(request, authorization):
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:], False

    cookie = request.cookies.get(COOKIE_NAME)

    if cookie:
        return cookie, True

    return None, False


def check_origin(request):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")

    if origin != expected:
        raise HTTPException(
            403,
            "Invalid request origin"
        )


def require(
    request: Request,
    authorization: str | None = Header(None)
):
    token, from_cookie = get_token(
        request,
        authorization
    )

    if not token:
        raise HTTPException(
            401,
            "Login required"
        )

    if (
        from_cookie
        and request.method not in (
            "GET", "HEAD", "OPTIONS"
        )
    ):
        check_origin(request)

    user = current_user(token)

    if not user:
        raise HTTPException(
            401,
            "Session expired or license revoked"
        )

    return user


def require_admin(user=Depends(require)):
    if not user["is_admin"]:
        raise HTTPException(
            403,
            "Owner access required"
        )

    return user


def get_member(conn, user_id, lock=False):
    query = """
        SELECT id,username,is_admin,suspended,
               created_at,last_login,login_count
        FROM users
        WHERE id=%s
    """

    if lock:
        query += " FOR UPDATE"

    row = conn.execute(
        query,
        (user_id,)
    ).fetchone()

    if not row:
        raise HTTPException(
            404,
            "Member not found"
        )

    return row


@app.post("/api/login")
def login(body: Login, response: Response):
    token = auth(
        body.username,
        body.password,
        body.license_key
    )

    if not token:
        raise HTTPException(
            401,
            "Invalid credentials, license, or account disabled"
        )

    if body.remember:
        with db() as conn:
            conn.execute(
                """UPDATE sessions
                   SET expires=%s
                   WHERE token_hash=%s""",
                (
                    time.time() + REMEMBER_SECONDS,
                    hash_key(token)
                )
            )

        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=REMEMBER_SECONDS,
            **COOKIE_OPTIONS
        )

        return {
            "ok": True,
            "remembered": True
        }

    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        secure=True,
        httponly=True,
        samesite="lax"
    )

    return {
        "token": token,
        "remembered": False
    }


@app.get("/api/me")
def me(user=Depends(require)):
    return user


@app.post("/api/logout")
def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(None)
):
    token, from_cookie = get_token(
        request,
        authorization
    )

    if from_cookie:
        check_origin(request)

    if token:
        with db() as conn:
            conn.execute(
                """DELETE FROM sessions
                   WHERE token_hash=%s""",
                (hash_key(token),)
            )

    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        secure=True,
        httponly=True,
        samesite="lax"
    )

    return {"ok": True}


@app.get("/api/platforms")
def platforms(user=Depends(require)):
    return {
        name: {
            "min": rule[0],
            "max": rule[1],
            "characters": "".join(
                sorted(rule[2])
            ),
            "checking": "manual"
        }
        for name, rule in RULES.items()
    }


@app.post("/api/generate")
def generate(
    body: Generate,
    user=Depends(require)
):
    try:
        names = candidates(
            body.platform,
            body.lengths,
            body.types,
            body.characters,
            body.prefix,
            body.suffix,
            body.amount,
            body.wordlist
        )

    except ValueError as error:
        raise HTTPException(
            400,
            str(error)
        )

    log_activity(
        user["id"],
        "generate",
        f"{body.platform}: {len(names)} usernames"
    )

    return {
        "usernames": names,
        "requested": body.amount,
        "generated": len(names),
        "platform": body.platform
    }


@app.post("/api/check")
def check(
    body: Check,
    user=Depends(require)
):
    if len(body.usernames) > 100000:
        raise HTTPException(
            400,
            "Too many names"
        )

    try:
        search_id, rows = start_search(
            user["id"],
            body.platform,
            body.usernames
        )

    except ValueError as error:
        raise HTTPException(
            400,
            str(error)
        )

    return {
        "search_id": search_id,
        "results": rows,
        "message": "Manual verification required."
    }


@app.post("/api/mark")
def mark(
    body: Mark,
    user=Depends(require)
):
    if body.status not in (
        "available",
        "claimed",
        "unknown"
    ):
        raise HTTPException(
            400,
            "Invalid status"
        )

    with db() as conn:
        row = conn.execute(
            """SELECT s.id,r.status
               FROM searches s
               JOIN results r
               ON r.search_id=s.id
               WHERE s.id=%s
               AND s.user_id=%s
               AND r.username=%s""",
            (
                body.search_id,
                user["id"],
                body.username
            )
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Result not found"
            )

        if row["status"] == "skipped":
            raise HTTPException(
                400,
                "Skipped results cannot be marked"
            )

        conn.execute(
            """UPDATE results
               SET status=%s
               WHERE search_id=%s
               AND username=%s""",
            (
                body.status,
                body.search_id,
                body.username
            )
        )

    return {
        "ok": True,
        "verification": "user-reported"
    }


@app.get("/api/history")
def history(user=Depends(require)):
    with db() as conn:
        searches = [
            dict(row)
            for row in conn.execute(
                """SELECT id,platform,created
                   FROM searches
                   WHERE user_id=%s
                   ORDER BY id DESC
                   LIMIT 50""",
                (user["id"],)
            )
        ]

        for search in searches:
            search["results"] = [
                dict(row)
                for row in conn.execute(
                    """SELECT username,status
                       FROM results
                       WHERE search_id=%s
                       LIMIT 200""",
                    (search["id"],)
                )
            ]

    return searches


@app.get("/api/favorites")
def favorites(user=Depends(require)):
    with db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT platform,username
                   FROM favorites
                   WHERE user_id=%s
                   ORDER BY username""",
                (user["id"],)
            )
        ]


@app.post("/api/favorites")
def add_favorite(
    body: Favorite,
    user=Depends(require)
):
    with db() as conn:
        conn.execute(
            """INSERT INTO favorites
               (user_id,platform,username)
               VALUES(%s,%s,%s)
               ON CONFLICT(
                   user_id,platform,username
               ) DO NOTHING""",
            (
                user["id"],
                body.platform,
                body.username
            )
        )

    return {"ok": True}


@app.delete(
    "/api/favorites/{platform}/{username}"
)
def delete_favorite(
    platform: str,
    username: str,
    user=Depends(require)
):
    with db() as conn:
        conn.execute(
            """DELETE FROM favorites
               WHERE user_id=%s
               AND platform=%s
               AND username=%s""",
            (
                user["id"],
                platform,
                username
            )
        )

    return {"ok": True}


@app.get("/api/presets")
def presets(user=Depends(require)):
    with db() as conn:
        return [
            {
                "name": row["name"],
                "config": json.loads(
                    row["config"]
                )
            }
            for row in conn.execute(
                """SELECT name,config
                   FROM presets
                   WHERE user_id=%s""",
                (user["id"],)
            )
        ]


@app.post("/api/presets")
def save_preset(
    body: Preset,
    user=Depends(require)
):
    if not 1 <= len(body.name) <= 50:
        raise HTTPException(
            400,
            "Invalid preset name"
        )

    with db() as conn:
        conn.execute(
            """INSERT INTO presets
               (user_id,name,config)
               VALUES(%s,%s,%s)
               ON CONFLICT(user_id,name)
               DO UPDATE SET
               config=excluded.config""",
            (
                user["id"],
                body.name,
                json.dumps(body.config)
            )
        )

    return {"ok": True}


# OWNER DASHBOARD V3


@app.get("/api/admin/stats")
def admin_stats(user=Depends(require_admin)):
    with db() as conn:
        users = conn.execute(
            """SELECT
               COUNT(*) AS total,
               COUNT(*) FILTER (
                   WHERE suspended=0
               ) AS enabled,
               COUNT(*) FILTER (
                   WHERE suspended=1
               ) AS suspended
               FROM users"""
        ).fetchone()

        licenses = conn.execute(
            """SELECT
               COUNT(*) AS total,
               COUNT(*) FILTER (
                   WHERE revoked=0
                   AND (
                       expires_at IS NULL
                       OR expires_at>%s
                   )
               ) AS active,
               COUNT(*) FILTER (
                   WHERE revoked=1
               ) AS revoked
               FROM licenses""",
            (now(),)
        ).fetchone()

        searches = conn.execute(
            """SELECT COUNT(*) AS total
               FROM searches"""
        ).fetchone()

        checked = conn.execute(
            """SELECT COUNT(*) AS total
               FROM results"""
        ).fetchone()

        generated = conn.execute(
            """SELECT details
               FROM activity
               WHERE action='generate'"""
        ).fetchall()

        total_generated = 0

        for row in generated:
            details = row["details"] or ""

            try:
                count = int(
                    details.split(": ")[1].split()[0]
                )
                total_generated += count
            except (IndexError, ValueError):
                pass

    return {
        "total_users": users["total"],
        "enabled_users": users["enabled"],
        "suspended_users": users["suspended"],
        "total_licenses": licenses["total"],
        "active_licenses": licenses["active"],
        "revoked_licenses": licenses["revoked"],
        "total_searches": searches["total"],
        "checked_usernames": checked["total"],
        "generated_usernames": total_generated
    }


@app.get("/api/admin/activity")
def admin_activity(
    limit: int = 50,
    user_id: int | None = None,
    user=Depends(require_admin)
):
    limit = max(1, min(limit, 200))

    with db() as conn:
        if user_id is None:
            rows = conn.execute(
                """SELECT a.id,a.user_id,
                          u.username,a.action,
                          a.details,a.created
                   FROM activity a
                   JOIN users u ON u.id=a.user_id
                   ORDER BY a.id DESC
                   LIMIT %s""",
                (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT a.id,a.user_id,
                          u.username,a.action,
                          a.details,a.created
                   FROM activity a
                   JOIN users u ON u.id=a.user_id
                   WHERE a.user_id=%s
                   ORDER BY a.id DESC
                   LIMIT %s""",
                (user_id, limit)
            ).fetchall()

    return [dict(row) for row in rows]


@app.get("/api/admin/users")
def admin_users(
    search: str = "",
    user=Depends(require_admin)
):
    with db() as conn:
        rows = conn.execute(
            """SELECT
                   u.id,u.username,u.is_admin,
                   u.suspended,u.created_at,
                   u.last_login,u.login_count,
                   (
                       SELECT COUNT(*)
                       FROM licenses l
                       WHERE l.user_id=u.id
                       AND l.revoked=0
                       AND (
                           l.expires_at IS NULL
                           OR l.expires_at>%s
                       )
                   ) AS active_licenses
               FROM users u
               WHERE LOWER(u.username)
               LIKE %s
               ORDER BY u.id""",
            (
                now(),
                "%" + search.lower().strip() + "%"
            )
        ).fetchall()

    return [dict(row) for row in rows]


@app.get("/api/admin/users/{user_id}")
def admin_user_details(
    user_id: int,
    user=Depends(require_admin)
):
    with db() as conn:
        member = get_member(
            conn,
            user_id
        )

        licenses = conn.execute(
            """SELECT id,user_id,revoked,expires_at
               FROM licenses
               WHERE user_id=%s
               ORDER BY id DESC""",
            (user_id,)
        ).fetchall()

        activity = conn.execute(
            """SELECT id,action,details,created
               FROM activity
               WHERE user_id=%s
               ORDER BY id DESC
               LIMIT 50""",
            (user_id,)
        ).fetchall()

    return {
        "member": dict(member),
        "licenses": [
            dict(row) for row in licenses
        ],
        "activity": [
            dict(row) for row in activity
        ]
    }


@app.post("/api/admin/users")
def admin_create_user(
    body: AdminUser,
    user=Depends(require_admin)
):
    username = body.username.strip()

    if not username:
        raise HTTPException(
            400,
            "Username is required"
        )

    try:
        user_id = create_user(
            username,
            body.password
        )

    except UniqueViolation:
        raise HTTPException(
            409,
            "Username already exists!"
        )

    except ValueError as error:
        raise HTTPException(
            400,
            str(error)
        )

    log_activity(
        user["id"],
        "member_created",
        f"Created member ID {user_id}"
    )

    return {
        "id": user_id,
        "username": username,
        "message": "Member successfully created!"
    }


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    user=Depends(require_admin)
):
    if user_id == user["id"]:
        raise HTTPException(
            403,
            "You cannot delete your own owner account"
        )

    with db() as conn:
        target = get_member(
            conn,
            user_id,
            lock=True
        )

        if target["is_admin"]:
            raise HTTPException(
                403,
                "Owner accounts cannot be deleted"
            )

        username = target["username"]

        conn.execute(
            """DELETE FROM users
               WHERE id=%s""",
            (user_id,)
        )

    log_activity(
        user["id"],
        "member_deleted",
        f"Deleted member ID {user_id}"
    )

    return {
        "ok": True,
        "message": (
            f"Member {username} successfully deleted!"
        )
    }


@app.post(
    "/api/admin/users/{user_id}/password"
)
def admin_reset_password(
    user_id: int,
    body: AdminPassword,
    user=Depends(require_admin)
):
    if len(body.password) < 12:
        raise HTTPException(
            400,
            "Password must be at least 12 characters"
        )

    hashed = bcrypt.hashpw(
        body.password.encode(),
        bcrypt.gensalt()
    )

    with db() as conn:
        target = get_member(
            conn,
            user_id,
            lock=True
        )

        if target["is_admin"]:
            raise HTTPException(
                403,
                "Owner passwords cannot be reset here"
            )

        conn.execute(
            """UPDATE users
               SET password=%s
               WHERE id=%s""",
            (hashed, user_id)
        )

        conn.execute(
            """DELETE FROM sessions
               WHERE user_id=%s""",
            (user_id,)
        )

    log_activity(
        user["id"],
        "password_reset",
        f"Reset password for member ID {user_id}"
    )

    return {
        "ok": True,
        "message": "Member password reset successfully!"
    }


@app.post(
    "/api/admin/users/{user_id}/suspension"
)
def admin_set_suspension(
    user_id: int,
    body: AdminSuspension,
    user=Depends(require_admin)
):
    with db() as conn:
        target = get_member(
            conn,
            user_id,
            lock=True
        )

        if target["is_admin"]:
            raise HTTPException(
                403,
                "Owner accounts cannot be suspended"
            )

        conn.execute(
            """UPDATE users
               SET suspended=%s
               WHERE id=%s""",
            (
                int(body.suspended),
                user_id
            )
        )

        if body.suspended:
            conn.execute(
                """DELETE FROM sessions
                   WHERE user_id=%s""",
                (user_id,)
            )

    log_activity(
        user["id"],
        (
            "member_suspended"
            if body.suspended
            else "member_unsuspended"
        ),
        f"Member ID {user_id}"
    )

    return {
        "ok": True,
        "suspended": body.suspended,
        "message": (
            "Member suspended successfully!"
            if body.suspended
            else "Member unsuspended successfully!"
        )
    }


@app.get("/api/admin/licenses")
def admin_licenses(user=Depends(require_admin)):
    with db() as conn:
        rows = conn.execute(
            """SELECT l.id,l.user_id,
                      u.username,l.revoked,
                      l.expires_at
               FROM licenses l
               JOIN users u
               ON u.id=l.user_id
               ORDER BY l.id DESC"""
        ).fetchall()

    return [dict(row) for row in rows]


@app.post(
    "/api/admin/users/{user_id}/licenses"
)
def admin_create_license(
    user_id: int,
    body: AdminLicense,
    user=Depends(require_admin)
):
    expires = parse_expiration(
        body.expires_at
    )

    with db() as conn:
        get_member(conn, user_id)

    try:
        key = create_license(
            user_id,
            expires
        )

    except ForeignKeyViolation:
        raise HTTPException(
            404,
            "User no longer exists"
        )

    log_activity(
        user["id"],
        "license_created",
        f"Created license for member ID {user_id}"
    )

    return {
        "license_key": key,
        "user_id": user_id,
        "expires_at": expires,
        "message": "License successfully generated!"
    }


@app.post(
    "/api/admin/licenses/{license_id}/renew"
)
def admin_renew_license(
    license_id: int,
    body: AdminLicense,
    user=Depends(require_admin)
):
    expires = parse_expiration(
        body.expires_at
    )

    with db() as conn:
        row = conn.execute(
            """SELECT id,user_id,revoked
               FROM licenses
               WHERE id=%s
               FOR UPDATE""",
            (license_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "License not found"
            )

        if row["revoked"]:
            raise HTTPException(
                400,
                "Reactivate this license before renewing it"
            )

        conn.execute(
            """UPDATE licenses
               SET expires_at=%s
               WHERE id=%s""",
            (expires, license_id)
        )

    log_activity(
        user["id"],
        "license_renewed",
        f"Renewed license ID {license_id}"
    )

    return {
        "ok": True,
        "expires_at": expires,
        "message": "License renewed successfully!"
    }


@app.post(
    "/api/admin/licenses/{license_id}/revoke"
)
def admin_revoke_license(
    license_id: int,
    user=Depends(require_admin)
):
    with db() as conn:
        row = conn.execute(
            """SELECT user_id,revoked
               FROM licenses
               WHERE id=%s
               FOR UPDATE""",
            (license_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "License not found"
            )

        if (
            row["user_id"] == user["id"]
            and not row["revoked"]
        ):
            other = conn.execute(
                """SELECT COUNT(*) AS total
                   FROM licenses
                   WHERE user_id=%s
                   AND id!=%s
                   AND revoked=0
                   AND (
                       expires_at IS NULL
                       OR expires_at>%s
                   )""",
                (
                    user["id"],
                    license_id,
                    now()
                )
            ).fetchone()["total"]

            if not other:
                raise HTTPException(
                    400,
                    "Cannot revoke your last active owner license"
                )

        conn.execute(
            """UPDATE licenses
               SET revoked=1
               WHERE id=%s""",
            (license_id,)
        )

        conn.execute(
            """DELETE FROM sessions
               WHERE user_id=%s""",
            (row["user_id"],)
        )

    log_activity(
        user["id"],
        "license_revoked",
        f"Revoked license ID {license_id}"
    )

    return {
        "ok": True,
        "message": "License successfully revoked!"
    }


@app.post(
    "/api/admin/licenses/{license_id}/reactivate"
)
def admin_reactivate_license(
    license_id: int,
    user=Depends(require_admin)
):
    with db() as conn:
        row = conn.execute(
            """SELECT id
               FROM licenses
               WHERE id=%s""",
            (license_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "License not found"
            )

        conn.execute(
            """UPDATE licenses
               SET revoked=0
               WHERE id=%s""",
            (license_id,)
        )

    log_activity(
        user["id"],
        "license_reactivated",
        f"Reactivated license ID {license_id}"
    )

    return {
        "ok": True,
        "message": "License successfully reactivated!"
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(
        ROOT / "frontend" / "index.html"
    )


app.mount(
    "/static",
    StaticFiles(
        directory=ROOT / "frontend"
    ),
    name="static"
)
