
import json
import time
from datetime import datetime, timezone
from pathlib import Path

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
    create_user, create_license
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
    lengths: list[int] = Field(default_factory=lambda: [3, 4, 5])
    types: list[str] = Field(default_factory=lambda: ["Random letters"])
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


def get_token(request: Request, authorization: str | None):
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:], False

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie, True

    return None, False


def require(
    request: Request,
    authorization: str | None = Header(None)
):
    token, from_cookie = get_token(request, authorization)

    if not token:
        raise HTTPException(401, "Login required")

    if from_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")

        if origin != expected:
            raise HTTPException(
                403, "Invalid request origin"
            )

    user = current_user(token)

    if not user:
        raise HTTPException(
            401, "Session expired or license revoked"
        )

    return user


def require_admin(user=Depends(require)):
    if not user["is_admin"]:
        raise HTTPException(403, "Owner access required")

    return user


@app.post("/api/login")
def login(body: Login, response: Response):
    token = auth(
        body.username,
        body.password,
        body.license_key
    )

    if not token:
        raise HTTPException(
            401, "Invalid credentials or license"
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
    token, from_cookie = get_token(request, authorization)

    if from_cookie:
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")

        if origin != expected:
            raise HTTPException(403, "Invalid request origin")

    if token:
        with db() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash=%s",
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
            "characters": "".join(sorted(rule[2])),
            "checking": "manual"
        }
        for name, rule in RULES.items()
    }


@app.post("/api/generate")
def generate(body: Generate, user=Depends(require)):
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
        raise HTTPException(400, str(error))

    return {
        "usernames": names,
        "requested": body.amount,
        "generated": len(names),
        "platform": body.platform
    }


@app.post("/api/check")
def check(body: Check, user=Depends(require)):
    if len(body.usernames) > 100000:
        raise HTTPException(400, "Too many names")

    try:
        search_id, rows = start_search(
            user["id"],
            body.platform,
            body.usernames
        )
    except ValueError as error:
        raise HTTPException(400, str(error))

    return {
        "search_id": search_id,
        "results": rows,
        "message": "Manual verification required."
    }


@app.post("/api/mark")
def mark(body: Mark, user=Depends(require)):
    if body.status not in ("available", "claimed", "unknown"):
        raise HTTPException(400, "Invalid status")

    with db() as conn:
        row = conn.execute(
            """SELECT s.id, r.status
               FROM searches s
               JOIN results r ON r.search_id=s.id
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
            raise HTTPException(404, "Result not found")

        if row["status"] == "skipped":
            raise HTTPException(
                400, "Skipped results cannot be marked"
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
def add_favorite(body: Favorite, user=Depends(require)):
    with db() as conn:
        conn.execute(
            """INSERT INTO favorites
               (user_id,platform,username)
               VALUES(%s,%s,%s)
               ON CONFLICT(user_id,platform,username)
               DO NOTHING""",
            (
                user["id"],
                body.platform,
                body.username
            )
        )

    return {"ok": True}


@app.delete("/api/favorites/{platform}/{username}")
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
            (user["id"], platform, username)
        )

    return {"ok": True}


@app.get("/api/presets")
def presets(user=Depends(require)):
    with db() as conn:
        return [
            {
                "name": row["name"],
                "config": json.loads(row["config"])
            }
            for row in conn.execute(
                """SELECT name,config
                   FROM presets
                   WHERE user_id=%s""",
                (user["id"],)
            )
        ]


@app.post("/api/presets")
def save_preset(body: Preset, user=Depends(require)):
    if not 1 <= len(body.name) <= 50:
        raise HTTPException(400, "Invalid preset name")

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


# OWNER DASHBOARD V2


@app.get("/api/admin/users")
def admin_users(user=Depends(require_admin)):
    with db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT id,username,is_admin
                   FROM users
                   ORDER BY id"""
            )
        ]


@app.post("/api/admin/users")
def admin_create_user(
    body: AdminUser,
    user=Depends(require_admin)
):
    username = body.username.strip()

    if not username:
        raise HTTPException(400, "Username is required")

    try:
        user_id = create_user(username, body.password)

    except UniqueViolation:
        raise HTTPException(
            409, "Username already exists!"
        )

    except ValueError as error:
        raise HTTPException(400, str(error))

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
            403, "You cannot delete your own owner account"
        )

    with db() as conn:
        target = conn.execute(
            """SELECT id,username,is_admin
               FROM users
               WHERE id=%s
               FOR UPDATE""",
            (user_id,)
        ).fetchone()

        if not target:
            raise HTTPException(404, "User not found")

        if target["is_admin"]:
            raise HTTPException(
                403, "Owner accounts cannot be deleted"
            )

        username = target["username"]

        conn.execute(
            "DELETE FROM users WHERE id=%s",
            (user_id,)
        )

    return {
        "ok": True,
        "message": (
            f"Member {username} successfully deleted!"
        )
    }


@app.get("/api/admin/licenses")
def admin_licenses(user=Depends(require_admin)):
    with db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT l.id,
                          l.user_id,
                          u.username,
                          l.revoked,
                          l.expires_at
                   FROM licenses l
                   JOIN users u ON u.id=l.user_id
                   ORDER BY l.id DESC"""
            )
        ]


@app.post("/api/admin/users/{user_id}/licenses")
def admin_create_license(
    user_id: int,
    body: AdminLicense,
    user=Depends(require_admin)
):
    expires = body.expires_at

    if expires:
        try:
            dt = datetime.fromisoformat(
                expires.replace("Z", "+00:00")
            )

            if dt.tzinfo is None:
                raise ValueError()

            if dt.astimezone(timezone.utc) <= datetime.now(
                timezone.utc
            ):
                raise ValueError()

            expires = dt.astimezone(
                timezone.utc
            ).isoformat()

        except ValueError:
            raise HTTPException(
                400, "Enter a valid future expiration date"
            )

    with db() as conn:
        target = conn.execute(
            "SELECT id FROM users WHERE id=%s",
            (user_id,)
        ).fetchone()

        if not target:
            raise HTTPException(404, "User not found")

    try:
        key = create_license(user_id, expires)
    except ForeignKeyViolation:
        raise HTTPException(
            404, "User no longer exists"
        )

    return {
        "license_key": key,
        "user_id": user_id,
        "expires_at": expires,
        "message": "License successfully generated!"
    }


@app.post("/api/admin/licenses/{license_id}/revoke")
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
            raise HTTPException(404, "License not found")

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

    return {
        "ok": True,
        "message": "License successfully revoked!"
    }


@app.post("/api/admin/licenses/{license_id}/reactivate")
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
            raise HTTPException(404, "License not found")

        conn.execute(
            """UPDATE licenses
               SET revoked=0
               WHERE id=%s""",
            (license_id,)
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
    StaticFiles(directory=ROOT / "frontend"),
    name="static"
)
