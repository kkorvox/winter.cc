
"""winter.cc username checker V6.9.

Discord Auto Check uses Discord's unauthenticated eligibility endpoint,
not Pomelo. Unclear responses never count as available.

Pomelo remains available for the Owner Dashboard diagnostic.
"""

import json
import re
import socket
import ssl
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SUPPORTED_PLATFORMS = (
    "Discord",
    "Roblox",
    "Instagram",
    "TikTok",
    "Telegram",
    "Minecraft",
)

NETWORK_CHECKERS = ("Roblox", "Minecraft")

MAX_BATCH_SIZE = 25
MAX_WORKERS = 4

DISCORD_URL = (
    "https://discord.com/api/v9/"
    "unique-username/username-attempt-unauthed"
)

POMELO_URL = (
    "https://api.pomelo.lixqa.cc/v1/lookups"
)

REQUEST_TIMEOUT = 7
DISCORD_TIMEOUT = 7
POMELO_TIMEOUT = 8

DISCORD_MIN_INTERVAL = 3.0
DISCORD_MAX_PER_BATCH = 4
POMELO_MIN_INTERVAL = 5.5

# Cache available names only briefly.
CACHE_SECONDS = {
    "available": 20,
    "claimed": 120,
    "invalid": 300,
}

_cache = {}
_cache_lock = threading.Lock()

_general_lock = threading.Lock()
_general_next = {}
_general_cooldown = {}

_discord_lock = threading.Lock()
_discord_next = 0.0
_discord_cooldown = 0.0

_pomelo_lock = threading.Lock()
_pomelo_next = 0.0


# =====================================
# RESULTS AND VALIDATION
# =====================================

def result(username, status="unknown", reason=""):
    return {
        "username": username,
        "status": status,
        "reason": reason,
    }


def validate_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        return False, "unsupported_platform"

    if not isinstance(username, str):
        return False, "invalid_username_type"

    if not username:
        return False, "empty_username"

    if platform == "Discord":
        name = username.lower()

        if not 2 <= len(name) <= 32:
            return False, "discord_invalid_length"

        if not re.fullmatch(r"[a-z0-9._]+", name):
            return False, "discord_invalid_characters"

        if ".." in name:
            return False, "discord_consecutive_periods"

        return True, "discord_format_valid"

    if platform == "Roblox":
        valid = (
            3 <= len(username) <= 20
            and re.fullmatch(
                r"[A-Za-z0-9_]+", username
            ) is not None
            and not username.startswith("_")
            and not username.endswith("_")
            and username.count("_") <= 1
        )

    elif platform == "Instagram":
        valid = (
            1 <= len(username) <= 30
            and re.fullmatch(
                r"[A-Za-z0-9._]+", username
            ) is not None
            and not username.endswith(".")
        )

    elif platform == "TikTok":
        valid = (
            2 <= len(username) <= 24
            and re.fullmatch(
                r"[A-Za-z0-9._]+", username
            ) is not None
            and not username.endswith(".")
        )

    elif platform == "Telegram":
        valid = (
            5 <= len(username) <= 32
            and re.fullmatch(
                r"[A-Za-z0-9_]+", username
            ) is not None
            and re.match(
                r"[A-Za-z]", username
            ) is not None
        )

    else:
        valid = (
            3 <= len(username) <= 16
            and re.fullmatch(
                r"[A-Za-z0-9_]+", username
            ) is not None
        )

    if valid:
        return True, "format_valid"

    return False, "invalid_format"


# =====================================
# CACHE
# =====================================

def _cache_get(platform, username):
    with _cache_lock:
        key = (platform, username.lower())
        entry = _cache.get(key)

        if not entry:
            return None

        value, expires = entry

        if expires <= time.monotonic():
            _cache.pop(key, None)
            return None

        answer = dict(value)
        answer["cached"] = True

        return answer


def _cache_set(platform, username, checked):
    ttl = CACHE_SECONDS.get(
        checked.get("status")
    )

    if ttl is None:
        return

    with _cache_lock:
        _cache[
            (platform, username.lower())
        ] = (
            dict(checked),
            time.monotonic() + ttl,
        )


# =====================================
# NETWORK HELPERS
# =====================================

def _retry_after(value, default=60.0):
    if value is None:
        return default

    try:
        return max(
            1.0,
            min(float(value), 3600.0)
        )
    except (TypeError, ValueError):
        pass

    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        seconds = (
            dt - datetime.now(timezone.utc)
        ).total_seconds()

        return max(
            1.0,
            min(seconds, 3600.0)
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return default


def _network_reason(error, prefix):
    reason = (
        error.reason
        if isinstance(error, URLError)
        else error
    )

    if isinstance(reason, ssl.SSLError):
        return prefix + "_ssl_error"

    if isinstance(reason, socket.gaierror):
        return prefix + "_dns_error"

    if isinstance(
        reason,
        (TimeoutError, socket.timeout),
    ):
        return prefix + "_timeout"

    if isinstance(
        reason,
        ConnectionRefusedError,
    ):
        return prefix + "_connection_refused"

    return prefix + "_network_error"


def _get_json(platform, url):
    with _general_lock:
        now = time.monotonic()

        if now < _general_cooldown.get(
            platform, 0
        ):
            return None, "rate_limited"

        delay = max(
            0.0,
            _general_next.get(platform, 0) - now,
        )

        _general_next[platform] = (
            max(
                now,
                _general_next.get(platform, 0),
            ) + 1.5
        )

    if delay:
        time.sleep(delay)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "winter.cc/6.9",
        },
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            return json.load(response), None

    except HTTPError as error:
        if error.code == 404:
            return None, "not_found"

        if error.code == 429:
            seconds = _retry_after(
                error.headers.get("Retry-After")
            )

            with _general_lock:
                _general_cooldown[platform] = max(
                    _general_cooldown.get(
                        platform, 0
                    ),
                    time.monotonic() + seconds,
                )

            return None, "rate_limited"

        return None, "http_" + str(error.code)

    except (
        URLError,
        TimeoutError,
        OSError,
    ):
        return None, "network_error"

    except (ValueError, UnicodeError):
        return None, "invalid_response"


# =====================================
# ROBLOX
# =====================================

def _check_roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday": (
            "2000-01-01T00:00:00Z"
        ),
        "request.context": "Signup",
    })

    url = (
        "https://auth.roblox.com"
        "/v1/usernames/validate?"
        + params
    )

    data, error = _get_json(
        "Roblox",
        url,
    )

    if error:
        return result(
            username,
            "unknown",
            error,
        )

    if not isinstance(data, dict):
        return result(
            username,
            "unknown",
            "roblox_invalid_response",
        )

    code = data.get("code")

    if type(code) is int and code == 0:
        return result(
            username,
            "available",
            "roblox_signup_validation",
        )

    if type(code) is int and code == 1:
        return result(
            username,
            "claimed",
            "roblox_signup_validation",
        )

    return result(
        username,
        "unknown",
        "roblox_rejected_or_inconclusive",
    )


# =====================================
# MINECRAFT
# =====================================

def _check_minecraft(username):
    url = (
        "https://api.mojang.com"
        "/users/profiles/minecraft/"
        + quote(username, safe="")
    )

    data, error = _get_json(
        "Minecraft",
        url,
    )

    if error == "not_found":
        return result(
            username,
            "unknown",
            "no_existing_profile",
        )

    if error:
        return result(
            username,
            "unknown",
            error,
        )

    if (
        isinstance(data, dict)
        and isinstance(
            data.get("id"), str
        )
        and bool(data["id"])
        and str(
            data.get("name", "")
        ).lower() == username.lower()
    ):
        return result(
            username,
            "claimed",
            "existing_profile",
        )

    return result(
        username,
        "unknown",
        "minecraft_inconclusive",
    )


# =====================================
# DISCORD INVALID STATUS DETECTION
# =====================================

_DISCORD_INVALID_CODES = {
    "BASE_TYPE_BAD_LENGTH",
    "USERNAME_INVALID_CHARACTERS",
    "USERNAME_INVALID_REPEATING_DOTS",
    "USERNAME_INVALID_CONTAINS",
}


def _discord_invalid_code(payload):
    found = set()

    def walk(node, depth=0):
        if depth > 4:
            return

        if isinstance(node, dict):
            code = node.get("code")

            if (
                isinstance(code, str)
                and code in _DISCORD_INVALID_CODES
            ):
                found.add(code)

            for key in (
                "errors",
                "username",
                "_errors",
            ):
                if key in node:
                    walk(
                        node[key],
                        depth + 1,
                    )

        elif isinstance(node, list):
            for item in node[:8]:
                walk(
                    item,
                    depth + 1,
                )

    walk(payload)

    return (
        sorted(found)[0]
        if found
        else None
    )


# =====================================
# DISCORD RATE LIMITS
# =====================================

def _discord_slot():
    global _discord_next

    with _discord_lock:
        now = time.monotonic()

        if now < _discord_cooldown:
            return (
                False,
                "discord_rate_limited",
            )

        delay = max(
            0.0,
            _discord_next - now,
        )

        _discord_next = (
            max(now, _discord_next)
            + DISCORD_MIN_INTERVAL
        )

    if delay:
        time.sleep(delay)

    with _discord_lock:
        if time.monotonic() < _discord_cooldown:
            return (
                False,
                "discord_rate_limited",
            )

    return True, ""


def _discord_block(seconds):
    global _discord_cooldown

    with _discord_lock:
        _discord_cooldown = max(
            _discord_cooldown,
            time.monotonic() + seconds,
        )


# =====================================
# DISCORD DIRECT CHECK
# =====================================

def _check_discord(username):
    cached = _cache_get(
        "Discord",
        username,
    )

    if cached:
        return cached

    allowed, reason = _discord_slot()

    if not allowed:
        return result(
            username,
            "unknown",
            reason,
        )

    request = Request(
        DISCORD_URL,
        data=json.dumps({
            "username": username,
        }).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/6.9",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=DISCORD_TIMEOUT,
        ) as response:
            data = json.load(response)

    except HTTPError as error:
        if error.code == 429:
            seconds = _retry_after(
                error.headers.get("Retry-After")
            )

            try:
                response = json.loads(
                    error.read(2048).decode("utf-8")
                )

                if isinstance(response, dict):
                    seconds = max(
                        seconds,
                        _retry_after(
                            response.get("retry_after")
                        ),
                    )

            except (
                ValueError,
                UnicodeError,
                OSError,
            ):
                pass

            _discord_block(seconds)

            return result(
                username,
                "unknown",
                "discord_rate_limited",
            )

        if error.code in (401, 403):
            _discord_block(180)

            return result(
                username,
                "unknown",
                "discord_access_blocked",
            )

        if error.code == 400:
            try:
                payload = json.loads(
                    error.read(4096).decode("utf-8")
                )

            except (
                ValueError,
                UnicodeError,
                OSError,
            ):
                payload = {}

            invalid = _discord_invalid_code(
                payload
            )

            if invalid:
                checked = result(
                    username,
                    "invalid",
                    "discord_" + invalid.lower(),
                )

                _cache_set(
                    "Discord",
                    username,
                    checked,
                )

                return checked

        return result(
            username,
            "unknown",
            "discord_http_" + str(error.code),
        )

    except (
        URLError,
        TimeoutError,
        OSError,
    ) as error:
        return result(
            username,
            "unknown",
            _network_reason(
                error,
                "discord",
            ),
        )

    except (
        ValueError,
        UnicodeError,
    ):
        return result(
            username,
            "unknown",
            "discord_invalid_response",
        )

    # Strict interpretation:
    # Only an actual JSON boolean is accepted.
    if (
        not isinstance(data, dict)
        or type(data.get("taken")) is not bool
    ):
        return result(
            username,
            "unknown",
            "discord_unexpected_response",
        )

    if data["taken"]:
        checked = result(
            username,
            "claimed",
            "discord_reported_taken",
        )

    else:
        checked = result(
            username,
            "available",
            "discord_reported_available",
        )

    checked["provider"] = "Discord"
    checked["cached"] = False

    _cache_set(
        "Discord",
        username,
        checked,
    )

    return checked


# =====================================
# OWNER DASHBOARD POMELO TEST
# =====================================

def test_pomelo_username(username):
    global _pomelo_next

    username = str(
        username
    ).strip().lower()

    valid, reason = validate_username(
        "Discord",
        username,
    )

    if not valid:
        return {
            "username": username,
            "status": "invalid",
            "reason": reason,
            "provider": "local_validation",
            "source": None,
        }

    with _pomelo_lock:
        now = time.monotonic()

        if now < _pomelo_next:
            return {
                "username": username,
                "status": "unknown",
                "reason": "pomelo_cooldown",
                "provider": "Pomelo",
                "source": None,
            }

        _pomelo_next = (
            now + POMELO_MIN_INTERVAL
        )

    payload = {
        "username": username,
        "publicLookup": True,
        "useCache": True,
        "includeSuggestions": False,
        "includeUser": False,
    }

    request = Request(
        POMELO_URL,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/6.9",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=POMELO_TIMEOUT,
        ) as response:
            data = json.load(response)

    except HTTPError as error:
        return {
            "username": username,
            "status": "unknown",
            "reason": (
                "pomelo_http_"
                + str(error.code)
            ),
            "provider": "Pomelo",
            "source": None,
        }

    except (
        URLError,
        TimeoutError,
        OSError,
    ) as error:
        return {
            "username": username,
            "status": "unknown",
            "reason": _network_reason(
                error,
                "pomelo",
            ),
            "provider": "Pomelo",
            "source": None,
        }

    except (
        ValueError,
        UnicodeError,
    ):
        return {
            "username": username,
            "status": "unknown",
            "reason": "pomelo_invalid_response",
            "provider": "Pomelo",
            "source": None,
        }

    details = (
        data.get("data")
        if isinstance(data, dict)
        else None
    )

    if not isinstance(details, dict):
        return {
            "username": username,
            "status": "unknown",
            "reason": "pomelo_unexpected_response",
            "provider": "Pomelo",
            "source": None,
        }

    provider_status = str(
        details.get("status", "")
    ).upper()

    status = {
        "AVAILABLE": "available",
        "TAKEN": "claimed",
        "RESERVED": "claimed",
    }.get(
        provider_status,
        "unknown",
    )

    if status != "unknown":
        reason = (
            "pomelo_reported_"
            + provider_status.lower()
        )
    else:
        reason = "pomelo_unexpected_status"

    return {
        "username": username,
        "status": status,
        "reason": reason,
        "provider": "Pomelo",
        "provider_status": provider_status,
        "source": str(
            details.get("servedFrom", "UNKNOWN")
        ),
        "duration": data.get("duration"),
    }


# =====================================
# SINGLE USERNAME CHECK
# =====================================

def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            "Unsupported platform"
        )

    username = str(username).strip()

    if platform == "Discord":
        username = username.lower()

    valid, reason = validate_username(
        platform,
        username,
    )

    if not valid:
        return result(
            username,
            "invalid",
            reason,
        )

    cached = _cache_get(
        platform,
        username,
    )

    if cached:
        return cached

    if platform == "Discord":
        return _check_discord(
            username
        )

    if platform == "Roblox":
        checked = _check_roblox(
            username
        )

    elif platform == "Minecraft":
        checked = _check_minecraft(
            username
        )

    else:
        checked = result(
            username,
            "unknown",
            "format_valid_but_availability_unverified",
        )

    checked["cached"] = False

    _cache_set(
        platform,
        username,
        checked,
    )

    return checked


# =====================================
# BATCH CHECK
# =====================================

def check_batch(platform, usernames):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            "Unsupported platform"
        )

    if not isinstance(
        usernames,
        (list, tuple),
    ):
        raise ValueError(
            "Usernames must be a list"
        )

    unique = []
    seen = set()

    for value in usernames:
        if not isinstance(value, str):
            continue

        username = value.strip()

        if not username:
            continue

        key = username.lower()

        if key not in seen:
            unique.append(username)
            seen.add(key)

    batch = unique[:MAX_BATCH_SIZE]

    if not batch:
        return {
            "platform": platform,
            "results": [],
            "checked": 0,
            "total": 0,
            "remaining": 0,
        }

    if platform == "Discord":
        results = []
        uncached_count = 0

        for username in batch:
            normalized = username.lower()

            valid, validation_reason = (
                validate_username(
                    platform,
                    normalized,
                )
            )

            if not valid:
                results.append(
                    result(
                        normalized,
                        "invalid",
                        validation_reason,
                    )
                )
                continue

            existing = _cache_get(
                platform,
                normalized,
            )

            if existing:
                results.append(existing)
                continue

            if (
                uncached_count
                >= DISCORD_MAX_PER_BATCH
            ):
                results.append(
                    result(
                        normalized,
                        "unknown",
                        "discord_batch_limit",
                    )
                )
                continue

            checked = check_username(
                platform,
                normalized,
            )

            results.append(checked)
            uncached_count += 1

            if checked.get("reason") in (
                "discord_rate_limited",
                "discord_access_blocked",
            ):
                for rest in batch[
                    len(results):
                ]:
                    results.append(
                        result(
                            rest.lower(),
                            "unknown",
                            "discord_paused",
                        )
                    )

                break

    elif platform in NETWORK_CHECKERS:
        with ThreadPoolExecutor(
            max_workers=min(
                MAX_WORKERS,
                len(batch),
            )
        ) as pool:
            results = list(
                pool.map(
                    lambda name: check_username(
                        platform,
                        name,
                    ),
                    batch,
                )
            )

    else:
        results = [
            check_username(
                platform,
                name,
            )
            for name in batch
        ]

    return {
        "platform": platform,
        "results": results,
        "checked": len(results),
        "total": len(unique),
        "remaining": (
            len(unique) - len(results)
        ),
    }
  
