
def init():
    with db() as c:
        c.executescript("""
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

def setup_owner():
    """Create the owner account and license from Render settings."""
    username = os.getenv("WINTER_OWNER_USERNAME", "").strip()
    password = os.getenv("WINTER_OWNER_PASSWORD", "")
    license_key = os.getenv("WINTER_OWNER_LICENSE", "")

    if not all((username, password, license_key)):
        return

    if not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", username):
        raise ValueError("Invalid owner username")

    if len(password) < 12:
        raise ValueError("Owner password must be at least 12 characters")

    if len(license_key) < 24:
        raise ValueError("Owner license key must be at least 24 characters")

    with db() as c:
        owner = c.execute(
            "SELECT id, is_admin FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if owner:
            if not owner["is_admin"]:
                raise ValueError("Owner username already belongs to another account")
            user_id = owner["id"]
        else:
            cur = c.execute(
                "INSERT INTO users(username,password,is_admin) VALUES(?,?,1)",
                (
                    username,
                    bcrypt.hashpw(password.encode(), bcrypt.gensalt())
                )
            )
            user_id = cur.lastrowid

        key_hash = hash_key(license_key)
        existing = c.execute(
            "SELECT user_id FROM licenses WHERE key_hash=?",
            (key_hash,)
        ).fetchone()

        if existing and existing["user_id"] != user_id:
            raise ValueError("Owner license is already assigned")

        if not existing:
            c.execute(
                "INSERT INTO licenses(key_hash,user_id,revoked) VALUES(?,?,0)",
                (key_hash, user_id)
            )


