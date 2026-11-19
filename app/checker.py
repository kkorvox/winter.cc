
"""winter.cc checker V7.5 — provider-aware cooldown reporting.

Complete replacement for app/checker.py.
Keeps Discord, Roblox, Minecraft, and the existing platform
validation, caching, and Owner Dashboard Pomelo diagnostic.
"""

import json
import math
import re
import socket
import ssl
import threading
import time

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SUPPORTED_PLATFORMS = (
    "Discord",
    "Roblox",
    "Instagram",
    "TikTok",
    "Telegram",
    "Minecraft",
)

MAX_BATCH_SIZE = 25
MAX_WORKERS = 4

DISCORD_MAX_PER_BATCH = 4
MINECRAFT_BULK_SIZE = 10

DISCORD_URL = (
    "https://discord.com/api/v9/"
    "unique-username/username-attempt-unauthed"
)

MINECRAFT_BULK_URL = (
    "https://api.mojang.com/profiles/minecraft"
)

POMELO_URL = (
    "https://api.pomelo.lixqa.cc/v1/lookups"
)

DISCORD_TIMEOUT = 8
REQUEST_TIMEOUT = 8
POMELO_TIMEOUT = 8

INTERVALS = {
    "Discord": 3.0,
    "Roblox": 1.5,
    "Minecraft": 3.0,
    "Pomelo": 5.5,
}

# Only used if the provider gives no valid retry duration.
FALLBACK_COOLDOWNS = {
    "Discord": 300.0,
    "Roblox": 90.0,
    "Minecraft": 90.0,
    "Pomelo": 90.0,
}

CACHE_TTLS = {
    "available": 15,
    "claimed": 120,
    "invalid": 300,
}

MAX_CACHE_ENTRIES = 2000

_cache = {}
_cache_lock = threading.Lock()

_locks = {
    name: threading.RLock()
    for name in INTERVALS
}

_next = {
    name: 0.0
    for name in INTERVALS
}

_cooldown_until = {
    name: 0.0
    for name in INTERVALS
}


# =====================================
# RESULTS
# =====================================

def result(username, status="unknown", reason="", **extras):
    return {
        "username": username,
        "status": status,
        "reason": reason,
        **extras,
    }


# =====================================
# VALIDATION
# =====================================

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

        if (
            re.fullmatch(r"[a-z0-9._]+", name) is None
            or ".." in name
        ):
            return False, "discord_invalid_characters"

        return True, "discord_format_valid"

    if platform == "Roblox":
        valid = (
            3 <= len(username) <= 20
            and re.fullmatch(
                r"[A-Za-z0-9_]+",
                username,
            ) is not None
            and not username.startswith("_")
            and not username.endswith("_")
            and username.count("_") <= 1
        )

    elif platform == "Instagram":
        valid = (
            1 <= len(username) <= 30
            and re.fullmatch(
                r"[A-Za-z0-9._]+",
                username,
            ) is not None
            and not username.endswith(".")
        )

    elif platform == "TikTok":
        valid = (
            2 <= len(username) <= 24
            and re.fullmatch(
                r"[A-Za-z0-9._]+",
                username,
            ) is not None
            and not username.endswith(".")
        )

    elif platform == "Telegram":
        valid = (
            5 <= len(username) <= 32
            and re.fullmatch(
                r"[A-Za-z0-9_]+",
                username,
            ) is not None
            and re.match(
                r"[A-Za-z]",
                username,
            ) is not None
        )

    else:
        valid = (
            3 <= len(username) <= 16
            and re.fullmatch(
                r"[A-Za-z0-9_]+",
                username,
            ) is not None
        )

    return (
        (True, "format_valid")
        if valid
        else (False, "invalid_format")
    )


# =====================================
# CACHE
# =====================================

def _cached(platform, username):
    key = (platform, username.lower())

    with _cache_lock:
        entry = _cache.get(key)

        if entry is None:
            return None

        saved, expiry = entry

        if expiry <= time.monotonic():
            _cache.pop(key, None)
            return None

        return {
            **saved,
            "username": username,
            "cached": True,
        }


def _save(platform, username, checked):
    ttl = CACHE_TTLS.get(
        checked.get("status")
    )

    if ttl is None:
        return

    with _cache_lock:
        now = time.monotonic()

        if len(_cache) >= MAX_CACHE_ENTRIES:
            for key in list(_cache):
                if _cache[key][1] <= now:
                    _cache.pop(key, None)

            while len(_cache) >= MAX_CACHE_ENTRIES:
                _cache.pop(next(iter(_cache)))

        _cache[
            (platform, username.lower())
        ] = (
            dict(checked),
            now + ttl,
        )


# =====================================
# NETWORK ERRORS
# =====================================

def _network_reason(error, platform):
    cause = (
        error.reason
        if isinstance(error, URLError)
        else error
    )

    if isinstance(cause, ssl.SSLError):
        return platform + "_ssl_error"

    if isinstance(cause, socket.gaierror):
        return platform + "_dns_error"

    if isinstance(
        cause,
        (TimeoutError, socket.timeout),
    ):
        return platform + "_timeout"

    if isinstance(
        cause,
        ConnectionRefusedError,
    ):
        return platform + "_connection_refused"

    return platform + "_network_error"


# =====================================
# RETRY-AFTER PARSING
# =====================================

def _seconds(value):
    if isinstance(value, bool) or value is None:
        return None

    try:
        seconds = float(value)

        if (
            math.isfinite(seconds)
            and seconds > 0
        ):
            return min(seconds, 86400.0)

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        pass

    if not isinstance(value, str):
        return None

    try:
        target = parsedate_to_datetime(value)

        if target.tzinfo is None:
            target = target.replace(
                tzinfo=timezone.utc
            )

        delta = (
            target - datetime.now(timezone.utc)
        ).total_seconds()

        if delta > 0:
            return min(delta, 86400.0)

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        pass

    return None


def _retry_after(headers, payload, platform):
    values = []

    for key in (
        "Retry-After",
        "X-RateLimit-Reset-After",
    ):
        value = (
            _seconds(headers.get(key))
            if headers is not None
            else None
        )

        if value is not None:
            values.append(value)

    if isinstance(payload, dict):
        value = _seconds(
            payload.get("retry_after")
        )

        if value is not None:
            values.append(value)

    if values:
        return max(values)

    return FALLBACK_COOLDOWNS[platform]


def _json_error(error):
    try:
        payload = json.loads(
            error.read(4096).decode("utf-8")
        )

        return (
            payload
            if isinstance(payload, dict)
            else {}
        )

    except (
        ValueError,
        UnicodeError,
        OSError,
    ):
        return {}


# =====================================
# PROVIDER COOLDOWN MANAGER
# =====================================

def _remaining(platform):
    return max(
        0.0,
        _cooldown_until[platform] -
        time.monotonic(),
    )


def _limit(platform, seconds):
    seconds = min(
        max(float(seconds), 1.0),
        86400.0,
    )

    _cooldown_until[platform] = max(
        _cooldown_until[platform],
        time.monotonic() + seconds + 1.0,
    )

    return _remaining(platform)


def _start_request(platform):
    """
    Called while holding the provider lock.

    Returns remaining cooldown seconds,
    or 0 when a request may proceed.
    """

    remaining = _remaining(platform)

    if remaining > 0:
        return remaining

    wait = max(
        0.0,
        _next[platform] - time.monotonic(),
    )

    if wait > 0:
        time.sleep(wait)

    remaining = _remaining(platform)

    if remaining > 0:
        return remaining

    _next[platform] = (
        time.monotonic() +
        INTERVALS[platform]
    )

    return 0.0


def _limited(username, platform, reason):
    return result(
        username,
        "unknown",
        reason,
        retry_after=round(
            _remaining(platform),
            2,
        ),
    )


# =====================================
# DISCORD INVALID RESPONSES
# =====================================

def _discord_invalid_code(payload):
    codes = {
        "BASE_TYPE_BAD_LENGTH",
        "USERNAME_INVALID_CHARACTERS",
        "USERNAME_INVALID_REPEATING_DOTS",
        "USERNAME_INVALID_CONTAINS",
    }

    found = []

    def scan(obj, depth=0):
        if depth > 4:
            return

        if isinstance(obj, dict):
            code = obj.get("code")

            if (
                isinstance(code, str)
                and code in codes
            ):
                found.append(code)

            for key in (
                "errors",
                "username",
                "_errors",
            ):
                if key in obj:
                    scan(
                        obj[key],
                        depth + 1,
                    )

        elif isinstance(obj, list):
            for item in obj[:8]:
                scan(
                    item,
                    depth + 1,
                )

    scan(payload)

    return (
        sorted(found)[0]
        if found
        else None
    )


# =====================================
# DISCORD AVAILABILITY
# =====================================

def _discord(username):
    with _locks["Discord"]:
        existing = _cached(
            "Discord",
            username,
        )

        if existing:
            return existing

        remaining = _start_request(
            "Discord"
        )

        if remaining:
            return _limited(
                username,
                "Discord",
                "discord_rate_limited",
            )

        request = Request(
            DISCORD_URL,
            data=json.dumps({
                "username": username.lower(),
            }).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "winter.cc/7.5",
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=DISCORD_TIMEOUT,
            ) as response:
                data = json.load(response)

                remaining_header = response.headers.get(
                    "X-RateLimit-Remaining"
                )

                reset_header = _seconds(
                    response.headers.get(
                        "X-RateLimit-Reset-After"
                    )
                )

                if (
                    remaining_header == "0"
                    and reset_header is not None
                ):
                    _limit(
                        "Discord",
                        reset_header,
                    )

        except HTTPError as error:
            if error.code == 429:
                payload = _json_error(error)

                seconds = _retry_after(
                    error.headers,
                    payload,
                    "Discord",
                )

                _limit(
                    "Discord",
                    seconds,
                )

                return _limited(
                    username,
                    "Discord",
                    "discord_rate_limited",
                )

            if error.code in (401, 403):
                return result(
                    username,
                    "unknown",
                    "discord_access_blocked",
                )

            if error.code == 400:
                code = _discord_invalid_code(
                    _json_error(error)
                )

                if code:
                    checked = result(
                        username,
                        "invalid",
                        "discord_" + code.lower(),
                    )

                    _save(
                        "Discord",
                        username,
                        checked,
                    )

                    return checked

            return result(
                username,
                "unknown",
                "discord_http_" +
                str(error.code),
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

        if (
            not isinstance(data, dict)
            or type(data.get("taken")) is not bool
        ):
            return result(
                username,
                "unknown",
                "discord_unexpected_response",
            )

        taken = data["taken"]

        checked = result(
            username,
            "claimed" if taken else "available",
            (
                "discord_reported_taken"
                if taken
                else "discord_reported_available"
            ),
            provider="Discord",
            cached=False,
        )

        _save(
            "Discord",
            username,
            checked,
        )

        return checked


# =====================================
# ROBLOX AVAILABILITY
# =====================================

def _roblox(username):
    with _locks["Roblox"]:
        existing = _cached(
            "Roblox",
            username,
        )

        if existing:
            return existing

        if _start_request("Roblox"):
            return _limited(
                username,
                "Roblox",
                "rate_limited",
            )

        url = (
            "https://auth.roblox.com"
            "/v1/usernames/validate?"
            + urlencode({
                "request.username": username,
                "request.birthday":
                    "2000-01-01T00:00:00Z",
                "request.context": "Signup",
            })
        )

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "winter.cc/7.5",
            },
        )

        try:
            with urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                data = json.load(response)

        except HTTPError as error:
            if error.code == 429:
                seconds = _retry_after(
                    error.headers,
                    _json_error(error),
                    "Roblox",
                )

                _limit(
                    "Roblox",
                    seconds,
                )

                return _limited(
                    username,
                    "Roblox",
                    "rate_limited",
                )

            return result(
                username,
                "unknown",
                "roblox_http_" +
                str(error.code),
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
                    "roblox",
                ),
            )

        except (
            ValueError,
            UnicodeError,
        ):
            return result(
                username,
                "unknown",
                "roblox_invalid_response",
            )

        if (
            not isinstance(data, dict)
            or type(data.get("code")) is not int
        ):
            return result(
                username,
                "unknown",
                "roblox_unexpected_response",
            )

        if data["code"] == 0:
            checked = result(
                username,
                "available",
                "roblox_signup_validation",
            )

        elif data["code"] == 1:
            checked = result(
                username,
                "claimed",
                "roblox_signup_validation",
            )

        else:
            checked = result(
                username,
                "unknown",
                "roblox_rejected_or_inconclusive",
            )

        _save(
            "Roblox",
            username,
            checked,
        )

        return checked


# =====================================
# MINECRAFT BULK CHECKING
# =====================================

def _minecraft_batch(usernames):
    if not usernames:
        return []

    if len(usernames) > MINECRAFT_BULK_SIZE:
        raise ValueError(
            "Minecraft batch exceeds 10 usernames"
        )

    with _locks["Minecraft"]:
        if _start_request("Minecraft"):
            return [
                _limited(
                    name,
                    "Minecraft",
                    "rate_limited",
                )
                for name in usernames
            ]

        request = Request(
            MINECRAFT_BULK_URL,
            data=json.dumps(
                usernames
            ).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "winter.cc/7.5",
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                data = json.load(response)

        except HTTPError as error:
            if error.code == 429:
                seconds = _retry_after(
                    error.headers,
                    _json_error(error),
                    "Minecraft",
                )

                _limit(
                    "Minecraft",
                    seconds,
                )

                return [
                    _limited(
                        name,
                        "Minecraft",
                        "rate_limited",
                    )
                    for name in usernames
                ]

            reason = (
                "minecraft_http_" +
                str(error.code)
            )

            return [
                result(name, "unknown", reason)
                for name in usernames
            ]

        except (
            URLError,
            TimeoutError,
            OSError,
        ) as error:
            reason = _network_reason(
                error,
                "minecraft",
            )

            return [
                result(name, "unknown", reason)
                for name in usernames
            ]

        except (
            ValueError,
            UnicodeError,
        ):
            return [
                result(
                    name,
                    "unknown",
                    "minecraft_invalid_response",
                )
                for name in usernames
            ]

        if (
            not isinstance(data, list)
            or any(
                not isinstance(item, dict)
                for item in data
            )
        ):
            return [
                result(
                    name,
                    "unknown",
                    "minecraft_unexpected_response",
                )
                for name in usernames
            ]

        claimed = {
            item["name"].lower()
            for item in data
            if (
                isinstance(item.get("name"), str)
                and isinstance(item.get("id"), str)
                and bool(item["id"])
            )
        }

        answers = []

        for username in usernames:
            if username.lower() in claimed:
                checked = result(
                    username,
                    "claimed",
                    "existing_profile",
                    cached=False,
                )

                _save(
                    "Minecraft",
                    username,
                    checked,
                )
            else:
                checked = result(
                    username,
                    "unknown",
                    "no_existing_profile",
                    cached=False,
                )

            answers.append(checked)

        return answers


# =====================================
# POMELO OWNER DIAGNOSTIC
# =====================================

def test_pomelo_username(username):
    username = str(
        username
    ).strip().lower()

    valid, reason = validate_username(
        "Discord",
        username,
    )

    if not valid:
        return result(
            username,
            "invalid",
            reason,
            provider="local_validation",
            source=None,
        )

    with _locks["Pomelo"]:
        if _start_request("Pomelo"):
            return result(
                username,
                "unknown",
                "pomelo_cooldown",
                provider="Pomelo",
                source=None,
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
                "User-Agent": "winter.cc/7.5",
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
            if error.code == 429:
                _limit(
                    "Pomelo",
                    _retry_after(
                        error.headers,
                        _json_error(error),
                        "Pomelo",
                    ),
                )

            return result(
                username,
                "unknown",
                "pomelo_http_" +
                str(error.code),
                provider="Pomelo",
                source=None,
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
                    "pomelo",
                ),
                provider="Pomelo",
                source=None,
            )

        except (
            ValueError,
            UnicodeError,
        ):
            return result(
                username,
                "unknown",
                "pomelo_invalid_response",
                provider="Pomelo",
                source=None,
            )

        details = (
            data.get("data")
            if isinstance(data, dict)
            else None
        )

        if not isinstance(details, dict):
            return result(
                username,
                "unknown",
                "pomelo_unexpected_response",
                provider="Pomelo",
                source=None,
            )

        reported = str(
            details.get("status", "")
        ).upper()

        status = {
            "AVAILABLE": "available",
            "TAKEN": "claimed",
            "RESERVED": "claimed",
        }.get(reported, "unknown")

        return result(
            username,
            status,
            (
                "pomelo_reported_" + reported.lower()
                if status != "unknown"
                else "pomelo_unexpected_status"
            ),
            provider="Pomelo",
            provider_status=reported,
            source=str(
                details.get(
                    "servedFrom",
                    "UNKNOWN",
                )
            ),
            duration=data.get("duration"),
        )


# =====================================
# SINGLE USERNAME CHECK
# =====================================

def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            "Unsupported platform"
        )

    username = str(username).strip()

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

    existing = _cached(
        platform,
        username,
    )

    if existing:
        return existing

    if platform == "Discord":
        return _discord(username)

    if platform == "Roblox":
        return _roblox(username)

    if platform == "Minecraft":
        return _minecraft_batch(
            [username]
        )[0]

    return result(
        username,
        "unknown",
        "format_valid_but_availability_unverified",
        cached=False,
    )


# =====================================
# BATCH CHECKING
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
        key = username.lower()

        if username and key not in seen:
            unique.append(username)
            seen.add(key)

    batch = unique[:MAX_BATCH_SIZE]

    answers = {}
    pending = []

    for username in batch:
        valid, reason = validate_username(
            platform,
            username,
        )

        if not valid:
            answers[username.lower()] = result(
                username,
                "invalid",
                reason,
            )
            continue

        existing = _cached(
            platform,
            username,
        )

        if existing:
            answers[username.lower()] = existing
        else:
            pending.append(username)

    if platform == "Minecraft":
        paused = False

        for index in range(
            0,
            len(pending),
            MINECRAFT_BULK_SIZE,
        ):
            group = pending[
                index:
                index + MINECRAFT_BULK_SIZE
            ]

            if paused:
                for username in group:
                    answers[username.lower()] = (
                        _limited(
                            username,
                            "Minecraft",
                            "rate_limited",
                        )
                    )
                continue

            checked = _minecraft_batch(group)

            for item in checked:
                answers[
                    item["username"].lower()
                ] = item

                if (
                    item.get("reason")
                    == "rate_limited"
                ):
                    paused = True

    elif platform == "Discord":
        attempted = 0
        paused = False

        for username in pending:
            if paused:
                checked = result(
                    username,
                    "unknown",
                    "discord_paused",
                    retry_after=round(
                        _remaining("Discord"),
                        2,
                    ),
                )

            elif attempted >= DISCORD_MAX_PER_BATCH:
                checked = result(
                    username,
                    "unknown",
                    "discord_batch_limit",
                )

            else:
                attempted += 1

                checked = _discord(username)

                if checked.get("reason") in (
                    "discord_rate_limited",
                    "discord_access_blocked",
                ):
                    paused = True

            answers[username.lower()] = checked

    else:
        for username in pending:
            answers[username.lower()] = (
                check_username(
                    platform,
                    username,
                )
            )

    return {
        "platform": platform,
        "results": [
            answers[username.lower()]
            for username in batch
        ],
        "checked": len(batch),
        "total": len(unique),
        "remaining": (
            len(unique) - len(batch)
        ),
    }
