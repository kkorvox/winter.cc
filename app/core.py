
import os
import sqlite3
import secrets
import hashlib
import random
import string
import time
import re
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

import bcrypt

DB = os.getenv(
    "WINTER_DB",
    str(Path(__file__).resolve().parent.parent / "winter.db")
)

WORDS = [
    "void", "ghost", "chrome", "angel", "vex", "nova",
    "frost", "pixel", "night", "lunar", "zero", "echo",
    "haze", "orbit", "drift", "glow", "shade", "storm",
    "azure", "bliss", "velvet", "static", "raven",
    "onyx", "winter"
]

RULES = {
    "Discord": (2, 32, set(string.ascii_lowercase + string.digits + "._")),
    "Roblox": (3, 20, set(string.ascii_letters + string.digits + "_")),
    "Instagram": (1, 30, set(string.ascii_letters + string.digits + "._")),
    "TikTok": (2, 24, set(string.ascii_letters + string.digits + "._")),
    "Telegram": (5, 32, set(string.ascii_letters + string.digits + "_")),
    "Minecraft": (3, 16, set(string.ascii_letters + string.digits + "_")),
}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password BLOB NOT NULL,
            is_admin INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY,
            key_hash TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            revoked INTEGER DEFAULT 0,
            expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            platform TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS results (
            search_id INTEGER,
            username TEXT,
            status TEXT,
            PRIMARY KEY(search_id, username)
        );

        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            platform TEXT,
            username TEXT,
            PRIMARY KEY(user_id, platform, username)
        );

        CREATE TABLE IF NOT EXISTS presets (
            user_id INTEGER,
            name TEXT,
            config TEXT,
            PRIMARY KEY(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS check_state (
            user_id INTEGER,
            platform TEXT,
            previous_search INTEGER,
            PRIMARY KEY(user_id, platform)
        );
        """)


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def create_user(username, password, admin=False):
    if not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", username):
        raise ValueError("Invalid username")

    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")

    hashed = bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt()
    )

    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO users(username,password,is_admin) VALUES(?,?,?)",
            (username, hashed, int(admin))
        )
        return cursor.lastrowid


def create_license(user_id, expires_at=None):
    key = "WCC-" + secrets.token_urlsafe(24)

    with db() as conn:
        conn.execute(
            "INSERT INTO licenses(key_hash,user_id,expires_at) VALUES(?,?,?)",
            (hash_key(key), user_id, expires_at)
        )

    return key


def setup_owner():
    username = os.getenv("WINTER_OWNER_USERNAME")
    password = os.getenv("WINTER_OWNER_PASSWORD")
    license_key = os.getenv("WINTER_OWNER_LICENSE")

    if not all((username, password, license_key)):
        return

    if not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", username):
        raise ValueError("Invalid owner username")

    if len(password) < 12:
        raise ValueError("Owner password must be at least 12 characters")

    if len(license_key) < 24:
        raise ValueError("Owner license must be at least 24 characters")

    with db() as conn:
        owner = conn.execute(
            "SELECT id,is_admin FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if owner:
            if not owner["is_admin"]:
                raise ValueError("Owner username belongs to a non-admin user")
            user_id = owner["id"]
        else:
            hashed = bcrypt.hashpw(
                password.encode(),
                bcrypt.gensalt()
            )
            cursor = conn.execute(
                "INSERT INTO users(username,password,is_admin) VALUES(?,?,1)",
                (username, hashed)
            )
            user_id = cursor.lastrowid

        key_hash = hash_key(license_key)

        existing = conn.execute(
            "SELECT user_id FROM licenses WHERE key_hash=?",
            (key_hash,)
        ).fetchone()

        if existing and existing["user_id"] != user_id:
            raise ValueError("Owner license belongs to another user")

        if not existing:
            conn.execute(
                "INSERT INTO licenses(key_hash,user_id) VALUES(?,?)",
                (key_hash, user_id)
            )


def auth(username, password, key):
    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if not user:
            return None

        if not bcrypt.checkpw(
            password.encode(),
            user["password"]
        ):
            return None

        license_row = conn.execute(
            """SELECT * FROM licenses
               WHERE key_hash=? AND user_id=? AND revoked=0""",
            (hash_key(key), user["id"])
        ).fetchone()

        if not license_row:
            return None

        if (
            license_row["expires_at"]
            and license_row["expires_at"] < now()
        ):
            return None

        token = secrets.token_urlsafe(32)

        conn.execute(
            "INSERT INTO sessions VALUES(?,?,?)",
            (
                hash_key(token),
                user["id"],
                time.time() + 12 * 3600
            )
        )

        return token


def current_user(token):
    with db() as conn:
        row = conn.execute(
            """SELECT u.id,u.username,u.is_admin
               FROM sessions s
               JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=?
               AND s.expires>?
               AND EXISTS (
                   SELECT 1 FROM licenses l
                   WHERE l.user_id=u.id
                   AND l.revoked=0
                   AND (
                       l.expires_at IS NULL
                       OR l.expires_at>?
                   )
               )""",
            (hash_key(token), time.time(), now())
        ).fetchone()

        return dict(row) if row else None


def candidates(
    platform,
    lengths,
    types,
    characters,
    prefix,
    suffix,
    count,
    wordlist=None
):
    if platform not in RULES:
        raise ValueError("Unsupported platform")

    minimum, maximum, allowed = RULES[platform]

    lengths = [
        int(x) for x in lengths
        if str(x).isdigit()
        and minimum <= int(x) <= maximum
    ]

    if not lengths:
        raise ValueError("No valid lengths")

    if not 1 <= count <= 100000:
        raise ValueError("Invalid amount")

    selections = [
        ("Lowercase", string.ascii_lowercase),
        ("Uppercase", string.ascii_uppercase),
        ("Numbers", string.digits),
        ("Underscores", "_"),
        ("Periods", ".")
    ]

    pool = "".join(
        chars for name, chars in selections
        if name in characters
    )

    pool = "".join(sorted(set(pool) & allowed))

    if not pool:
        raise ValueError("No permitted characters selected")

    if any(char not in allowed for char in prefix + suffix):
        raise ValueError("Invalid prefix or suffix")

    if len(prefix) + len(suffix) > max(lengths):
        raise ValueError("Prefix and suffix are too long")

    types = types or ["Random letters"]

    words = [
        word.strip()
        for word in (wordlist or WORDS)
        if isinstance(word, str) and word.strip()
    ]

    words = [
        word for word in words
        if all(char in allowed for char in word)
    ] or WORDS

    rng = random.SystemRandom()
    seen = set()
    output = []
    attempts = 0

    consonants = "bcdfghjklmnpqrstvwxyz"
    vowels = "aeiou"

    while (
        len(output) < count
        and attempts < max(1000, count * 35)
    ):
        attempts += 1

        size = rng.choice(lengths)
        middle = size - len(prefix) - len(suffix)

        if middle < 0:
            continue

        mode = rng.choice(types)

        if mode == "Numbers only":
            base = "".join(
                rng.choices(string.digits, k=middle)
            )

        elif mode in ("Real words / OG", "Aesthetic"):
            word = rng.choice(words)
            base = (
                word[:middle]
                if len(word) >= middle
                else word + "".join(
                    rng.choices(pool, k=middle - len(word))
                )
            )

        elif mode == "Pronounceable":
            base = "".join(
                rng.choice(
                    consonants if i % 2 == 0 else vowels
                )
                for i in range(middle)
            )

        elif mode == "Letters + numbers":
            mixed = "".join(
                char for char in pool
                if char.isalnum()
            )
            base = "".join(
                rng.choices(mixed or pool, k=middle)
            )

        else:
            base = "".join(
                rng.choices(pool, k=middle)
            )

        name = prefix + base + suffix

        if platform == "Discord":
            name = name.lower()

        if len(name) not in lengths:
            continue

        if any(char not in allowed for char in name):
            continue

        if name not in seen:
            seen.add(name)
            output.append(name)

    return output


def start_search(user_id, platform, names):
    if platform not in RULES:
        raise ValueError("Unsupported platform")

    names = list(dict.fromkeys(names))

    with db() as conn:
        previous = conn.execute(
            """SELECT previous_search
               FROM check_state
               WHERE user_id=? AND platform=?""",
            (user_id, platform)
        ).fetchone()

        skipped = set()

        if previous:
            skipped = {
                row["username"]
                for row in conn.execute(
                    """SELECT username FROM results
                       WHERE search_id=? AND status=?""",
                    (
                        previous["previous_search"],
                        "available"
                    )
                )
            }

        cursor = conn.execute(
            "INSERT INTO searches(user_id,platform,created) VALUES(?,?,?)",
            (user_id, platform, now())
        )

        search_id = cursor.lastrowid

        rows = [
            {
                "username": name,
                "status": (
                    "skipped"
                    if name in skipped
                    else "unknown"
                )
            }
            for name in names
        ]

        conn.executemany(
            """INSERT INTO results
               (search_id,username,status)
               VALUES(?,?,?)""",
            [
                (
                    search_id,
                    row["username"],
                    row["status"]
                )
                for row in rows
            ]
        )

        conn.execute(
            """INSERT INTO check_state
               (user_id,platform,previous_search)
               VALUES(?,?,?)
               ON CONFLICT(user_id,platform)
               DO UPDATE SET
               previous_search=excluded.previous_search""",
            (user_id, platform, search_id)
        )

    return search_id, rows
