
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


class Login(BaseModel):
    username: str
    password: str
    license_key: str


class Generate(BaseModel):
    platform: str = "Discord"
    lengths: list[int] = Field(default_factory=lambda: [3, 4, 5])
    types: list[str] = Field(default_factory=lambda: ["Random letters"])
    characters: list[str] = Field(default_factory=lambda: ["Lowercase", "Numbers"])
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


def require(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    u = current_user(authorization[7:])
    if not u:
        raise HTTPException(401, "Session expired or license revoked")
    return u


def require_admin(u=Depends(require)):
    if not u["is_admin"]:
        raise HTTPException(403, "Owner access required")
    return u


@app.post("/api/login")
def login(body: Login):
    token = auth(body.username, body.password, body.license_key)
    if not token:
        raise HTTPException(401, "Invalid credentials or license")
    return {"token": token}


@app.get("/api/me")
def me(u=Depends(require)):
    return u


@app.post("/api/logout")
def logout(authorization: str | None = Header(None), u=Depends(require)):
    with db() as c:
        c.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hash_key(authorization[7:]),)
        )
    return {"ok": True}


@app.get("/api/platforms")
def platforms(u=Depends(require)):
    return {
        k: {
            "min": v[0],
            "max": v[1],
            "characters": "".join(sorted(v[2])),
            "checking": "manual"
        }
        for k, v in RULES.items()
    }


@app.post("/api/generate")
def generate(b: Generate, u=Depends(require)):
    try:
        names = candidates(
            b.platform, b.lengths, b.types,
            b.characters, b.prefix, b.suffix,
            b.amount, b.wordlist
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "usernames": names,
        "requested": b.amount,
        "generated": len(names),
        "platform": b.platform
    }


@app.post("/api/check")
def check(b: Check, u=Depends(require)):
    try:
        sid, rows = start_search(u["id"], b.platform, b.usernames)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "search_id": sid,
        "results": rows,
        "message": "Manual verification required."
    }


@app.post("/api/mark")
def mark(b: Mark, u=Depends(require)):
    if b.status not in ("available", "claimed", "unknown"):
        raise HTTPException(400, "Invalid status")
    with db() as c:
        row = c.execute(
            """SELECT s.id, r.status FROM searches s
               JOIN results r ON r.search_id=s.id
               WHERE s.id=? AND s.user_id=? AND r.username=?""",
            (b.search_id, u["id"], b.username)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Result not found")
        if row["status"] == "skipped":
            raise HTTPException(400, "Skipped results cannot be marked")
        c.execute(
            "UPDATE results SET status=? WHERE search_id=? AND username=?",
            (b.status, b.search_id, b.username)
        )
    return {"ok": True, "verification": "user-reported"}


@app.get("/api/history")
def history(u=Depends(require)):
    with db() as c:
        searches = [
            dict(r) for r in c.execute(
                "SELECT id,platform,created FROM searches "
                "WHERE user_id=? ORDER BY id DESC LIMIT 50",
                (u["id"],)
            )
        ]
        for s in searches:
            s["results"] = [
                dict(r) for r in c.execute(
                    "SELECT username,status FROM results "
                    "WHERE search_id=? LIMIT 200",
                    (s["id"],)
                )
            ]
    return searches


@app.get("/api/favorites")
def favorites(u=Depends(require)):
    with db() as c:
        return [
            dict(r) for r in c.execute(
                "SELECT platform,username FROM favorites "
                "WHERE user_id=? ORDER BY username",
                (u["id"],)
            )
        ]


@app.post("/api/favorites")
def add_favorite(b: Favorite, u=Depends(require)):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO favorites VALUES(?,?,?)",
            (u["id"], b.platform, b.username)
        )
    return {"ok": True}


@app.delete("/api/favorites/{platform}/{username}")
def del_favorite(platform: str, username: str, u=Depends(require)):
    with db() as c:
        c.execute(
            "DELETE FROM favorites WHERE user_id=? "
            "AND platform=? AND username=?",
            (u["id"], platform, username)
        )
    return {"ok": True}


@app.get("/api/presets")
def presets(u=Depends(require)):
    with db() as c:
        return [
            {"name": r["name"], "config": json.loads(r["config"])}
            for r in c.execute(
                "SELECT name,config FROM presets WHERE user_id=?",
                (u["id"],)
            )
        ]


@app.post("/api/presets")
def save_preset(b: Preset, u=Depends(require)):
    if not 1 <= len(b.name) <= 50:
        raise HTTPException(400, "Invalid preset name")
    with db() as c:
        c.execute(
            """INSERT INTO presets VALUES(?,?,?)
               ON CONFLICT(user_id,name)
               DO UPDATE SET config=excluded.config""",
            (u["id"], b.name, json.dumps(b.config))
        )
    return {"ok": True}


# OWNER DASHBOARD API

@app.get("/api/admin/users")
def admin_users(u=Depends(require_admin)):
    with db() as c:
        return [
            dict(r) for r in c.execute(
                "SELECT id,username,is_admin FROM users ORDER BY id"
            )
        ]


@app.post("/api/admin/users")
def admin_create_user(b: AdminUser, u=Depends(require_admin)):
    try:
        user_id = create_user(b.username, b.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(409, "Username may already exist")
    return {"id": user_id, "username": b.username}


@app.get("/api/admin/licenses")
def admin_licenses(u=Depends(require_admin)):
    with db() as c:
        rows = c.execute(
            """SELECT l.id,l.user_id,users.username,
                      l.revoked,l.expires_at
               FROM licenses l
               JOIN users ON users.id=l.user_id
               ORDER BY l.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/admin/users/{user_id}/licenses")
def admin_create_license(
    user_id: int,
    b: AdminLicense,
    u=Depends(require_admin)
):
    expires = b.expires_at

    if expires:
        try:
            dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError()
            if dt.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                raise ValueError()
            expires = dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            raise HTTPException(400, "Enter a valid future expiration date")

    with db() as c:
        user = c.execute(
            "SELECT id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user:
            raise HTTPException(404, "User not found")

    key = create_license(user_id, expires)
    return {
        "license_key": key,
        "user_id": user_id,
        "expires_at": expires,
        "message": "Save this key now. It cannot be viewed again."
    }


@app.post("/api/admin/licenses/{license_id}/revoke")
def admin_revoke_license(license_id: int, u=Depends(require_admin)):
    with db() as c:
        row = c.execute(
            "SELECT user_id,revoked FROM licenses WHERE id=?",
            (license_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "License not found")

        if row["user_id"] == u["id"] and not row["revoked"]:
            other = c.execute(
                """SELECT COUNT(*) FROM licenses
                   WHERE user_id=? AND id!=?
                   AND revoked=0
                   AND (expires_at IS NULL OR expires_at>?)""",
                (u["id"], license_id, now())
            ).fetchone()[0]
            if not other:
                raise HTTPException(
                    400, "Cannot revoke your last active owner license"
                )

        c.execute(
            "UPDATE licenses SET revoked=1 WHERE id=?",
            (license_id,)
        )
        c.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (row["user_id"],)
        )
    return {"ok": True}


@app.post("/api/admin/licenses/{license_id}/reactivate")
def admin_reactivate_license(license_id: int, u=Depends(require_admin)):
    with db() as c:
        row = c.execute(
            "SELECT id FROM licenses WHERE id=?", (license_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "License not found")
        c.execute(
            "UPDATE licenses SET revoked=0 WHERE id=?",
            (license_id,)
        )
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


app.mount(
    "/static",
    StaticFiles(directory=ROOT / "frontend"),
    name="static"
)
