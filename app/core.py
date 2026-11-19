
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

def setup_owner():
    return None
