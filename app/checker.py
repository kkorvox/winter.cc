
"""winter.cc checker V7.3.

Bulk Minecraft lookups, direct Discord checks,
caching, and provider-aware rate limiting.

Complete replacement for app/checker.py.
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

REQUEST_TIMEOUT = 8
DISCORD_TIMEOUT = 7
POMELO_TIMEOUT = 8

DISCORD_MIN_INTERVAL = 3.0
MINECRAFT_MIN_INTERVAL = 3.0
POMELO_MIN_INTERVAL = 5.5

DISCORD_MAX_PER_BATCH = 4
MINECRAFT_BULK_SIZE = 10

MAX_CACHE_ENTRIES = 2000

CACHE_SECONDS = {
    "available": 15,
    "claimed": 120,
    "invalid": 300,
}

_cache = {}
_cache_lock = threading.RLock()

_slots_lock = threading.Lock()
_next_request = {}
_cooldowns = {}

_discord_lock = threading.Lock()
_pomelo_lock = threading.Lock()


# =====================================
# RESULTS
# =====================================

def result(username, status="unknown", reason=""):
    return {
        "username": username,
        "status": status,
        "reason": reason,
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

    return (
        (True, "format_valid")
        if valid
        else (False, "invalid_format")
    )


# =====================================
# CACHE
# =====================================

def _cache_get(platform, username):
    key = (platform, username.lower())

    with _cache_lock:
        entry = _cache.get(key)

        if entry is None:
            return None

        value, expiry = entry

        if time.monotonic() >= expiry:
            _cache.pop(key, None)
            return None

        return {
            **value,
            "cached": True,
        }


def _cache_set(platform, username, checked):
    ttl = CACHE_SECONDS.get(
        checked.get("status")
    )

    if ttl is None:
        return

    with _cache_lock:
        now = time.monotonic()

        if len(_cache) >= MAX_CACHE_ENTRIES:
            for key in list(_cache):
                if _cache[key][1] < now:
                    _cache.pop(key, None)

            while len(_cache) >= MAX_CACHE_ENTRIES:
                _cache.pop(
                    next(iter(_cache))
                )

        _cache[
            (platform, username.lower())
        ] = (
            dict(checked),
            now + ttl,
        )


# =====================================
# RATE LIMIT HELPERS
# =====================================

def _retry_after(value, default=60.0):
    if value is None:
        return default

    try:
        return max(
            1.0,
            min(float(value), 3600.0),
        )

    except (TypeError, ValueError):
        pass

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return max(
            1.0,
            min(
                (
                    parsed -
                    datetime.now(timezone.utc)
                ).total_seconds(),
                3600.0,
            ),
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return default


def _network_reason(error, prefix):
    cause = (
        error.reason
        if isinstance(error, URLError)
        else error
    )

    if isinstance(cause, ssl.SSLError):
        return prefix + "_ssl_error"

    if isinstance(cause, socket.gaierror):
        return prefix + "_dns_error"

    if isinstance(
        cause,
        (TimeoutError, socket.timeout),
    ):
        return prefix + "_timeout"

    if isinstance(
        cause,
        ConnectionRefusedError,
    ):
        return prefix + "_connection_refused"

    return prefix + "_network_error"


def _wait_slot(platform, interval):
    with _slots_lock:
        now = time.monotonic()

        if now < _cooldowns.get(platform, 0):
            return False

        when = max(
            now,
            _next_request.get(platform, 0),
        )

        _next_request[platform] = (
            when + interval
        )

    delay = max(
        0.0,
        when - time.monotonic(),
    )

    if delay:
        time.sleep(delay)

    with _slots_lock:
        return (
            time.monotonic()
            >= _cooldowns.get(platform, 0)
        )


def _block(platform, seconds):
    with _slots_lock:
        _cooldowns[platform] = max(
            _cooldowns.get(platform, 0),
            time.monotonic() + seconds,
        )


# =====================================
# ROBLOX
# =====================================

def _roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday":
            "2000-01-01T00:00:00Z",
        "request.context": "Signup",
    })

    if not _wait_slot("Roblox", 1.5):
        return result(
            username,
            "unknown",
            "rate_limited",
        )

    request = Request(
        "https://auth.roblox.com"
        "/v1/usernames/validate?"
        + params,
        headers={
            "Accept": "application/json",
            "User-Agent": "winter.cc/7.3",
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
            _block(
                "Roblox",
                _retry_after(
                    error.headers.get(
                        "Retry-After"
                    )
                ),
            )

            return result(
                username,
                "unknown",
                "rate_limited",
            )

        return result(
            username,
            "unknown",
            "roblox_http_"
            + str(error.code),
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

    except (ValueError, UnicodeError):
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

    _cache_set(
        "Roblox",
        username,
        checked,
    )

    return checked


# =====================================
# MINECRAFT BULK CHECKER
# =====================================

def _minecraft_batch(usernames):
    if not usernames:
        return []

    if len(usernames) > MINECRAFT_BULK_SIZE:
        raise ValueError(
            "Minecraft batch exceeds 10 names"
        )

    if not _wait_slot(
        "Minecraft",
        MINECRAFT_MIN_INTERVAL,
    ):
        return [
            result(
                name,
                "unknown",
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
            "User-Agent": "winter.cc/7.3",
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
            _block(
                "Minecraft",
                _retry_after(
                    error.headers.get(
                        "Retry-After"
                    ),
                    default=35.0,
                ),
            )

            reason = "rate_limited"

        else:
            reason = (
                "minecraft_http_"
                + str(error.code)
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

    except (ValueError, UnicodeError):
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
        or not all(
            isinstance(item, dict)
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

    claimed = set()

    for item in data:
        name = item.get("name")
        uid = item.get("id")

        if (
            isinstance(name, str)
            and isinstance(uid, str)
            and uid
        ):
            claimed.add(name.lower())

    answers = []

    for username in usernames:
        if username.lower() in claimed:
            checked = result(
                username,
                "claimed",
                "existing_profile",
            )

            _cache_set(
                "Minecraft",
                username,
                checked,
            )

        else:
            checked = result(
                username,
                "unknown",
                "no_existing_profile",
            )

        checked["cached"] = False
        answers.append(checked)

    return answers


# =====================================
# DISCORD INVALID RESPONSES
# =====================================

def _discord_invalid_code(payload):
    invalid_codes = {
        "BASE_TYPE_BAD_LENGTH",
        "USERNAME_INVALID_CHARACTERS",
        "USERNAME_INVALID_REPEATING_DOTS",
        "USERNAME_INVALID_CONTAINS",
    }

    found = set()

    def visit(node, depth=0):
        if depth > 4:
            return

        if isinstance(node, dict):
            value = node.get("code")

            if (
                isinstance(value, str)
                and value in invalid_codes
            ):
                found.add(value)

            for key in (
                "errors",
                "username",
                "_errors",
            ):
                if key in node:
                    visit(
                        node[key],
                        depth + 1,
                    )

        elif isinstance(node, list):
            for value in node[:8]:
                visit(
                    value,
                    depth + 1,
                )

    visit(payload)

    return (
        sorted(found)[0]
        if found
        else None
    )


# =====================================
# DISCORD DIRECT CHECKER
# =====================================

def _discord(username):
    with _discord_lock:
        cached = _cache_get(
            "Discord",
            username,
        )

        if cached:
            return cached

        if not _wait_slot(
            "Discord",
            DISCORD_MIN_INTERVAL,
        ):
            return result(
                username,
                "unknown",
                "discord_rate_limited",
            )

        request = Request(
            DISCORD_URL,
            data=json.dumps({
                "username": username,
            }).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "winter.cc/7.0",
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=DISCORD_TIMEOUT,
            ) as response:
                data = json.load(response)

                remaining = response.headers.get(
                    "X-RateLimit-Remaining"
                )

                reset = response.headers.get(
                    "X-RateLimit-Reset-After"
                )

                if (
                    remaining is not None
                    and reset is not None
                ):
                    try:
                        if int(remaining) <= 0:
                            _block(
                                "Discord",
                                max(
                                    1.0,
                                    min(
                                        float(reset),
                                        3600,
                                    ),
                                ),
                            )

                    except (
                        TypeError,
                        ValueError,
                        OverflowError,
                    ):
                        pass

        except HTTPError as error:
            if error.code == 429:
                seconds = _retry_after(
                    error.headers.get(
                        "Retry-After"
                    )
                )

                try:
                    body = json.loads(
                        error.read(2048)
                        .decode("utf-8")
                    )

                    if isinstance(body, dict):
                        seconds = max(
                            seconds,
                            _retry_after(
                                body.get(
                                    "retry_after"
                                ),
                                default=0.0,
                            ),
                        )

                except (
                    OSError,
                    ValueError,
                    UnicodeError,
                ):
                    pass

                _block(
                    "Discord",
                    seconds,
                )

                return result(
                    username,
                    "unknown",
                    "discord_rate_limited",
                )

            if error.code in (401, 403):
                _block(
                    "Discord",
                    180.0,
                )

                return result(
                    username,
                    "unknown",
                    "discord_access_blocked",
                )

            if error.code == 400:
                try:
                    body = json.loads(
                        error.read(4096)
                        .decode("utf-8")
                    )

                except (
                    OSError,
                    ValueError,
                    UnicodeError,
                ):
                    body = {}

                invalid = _discord_invalid_code(
                    body
                )

                if invalid:
                    checked = result(
                        username,
                        "invalid",
                        "discord_"
                        + invalid.lower(),
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
                "discord_http_"
                + str(error.code),
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

        checked = result(
            username,
            (
                "claimed"
                if data["taken"]
                else "available"
            ),
            (
                "discord_reported_taken"
                if data["taken"]
                else "discord_reported_available"
            ),
        )

        checked.update({
            "provider": "Discord",
            "cached": False,
        })

        _cache_set(
            "Discord",
            username,
            checked,
        )

        return checked


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
        return {
            **result(
                username,
                "invalid",
                reason,
            ),
            "provider": "local_validation",
            "source": None,
        }

    if not _wait_slot(
        "Pomelo",
        POMELO_MIN_INTERVAL,
    ):
        return {
            **result(
                username,
                "unknown",
                "pomelo_cooldown",
            ),
            "provider": "Pomelo",
            "source": None,
        }

    request = Request(
        POMELO_URL,
        data=json.dumps({
            "username": username,
            "publicLookup": True,
            "useCache": True,
            "includeSuggestions": False,
            "includeUser": False,
        }).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/7.3",
        },
        method="POST",
    )

    def unknown(why):
        return {
            **result(
                username,
                "unknown",
                why,
            ),
            "provider": "Pomelo",
            "source": None,
        }

    try:
        with urlopen(
            request,
            timeout=POMELO_TIMEOUT,
        ) as response:
            data = json.load(response)

    except HTTPError as error:
        return unknown(
            "pomelo_http_"
            + str(error.code)
        )

    except (
        URLError,
        TimeoutError,
        OSError,
    ) as error:
        return unknown(
            _network_reason(
                error,
                "pomelo",
            )
        )

    except (
        ValueError,
        UnicodeError,
    ):
        return unknown(
            "pomelo_invalid_response"
        )

    details = (
        data.get("data")
        if isinstance(data, dict)
        else None
    )

    if not isinstance(details, dict):
        return unknown(
            "pomelo_unexpected_response"
        )

    raw_status = str(
        details.get("status", "")
    ).upper()

    status = {
        "AVAILABLE": "available",
        "TAKEN": "claimed",
        "RESERVED": "claimed",
    }.get(
        raw_status,
        "unknown",
    )

    return {
        "username": username,
        "status": status,
        "reason": (
            "pomelo_reported_"
            + raw_status.lower()
            if status != "unknown"
            else "pomelo_unexpected_status"
        ),
        "provider": "Pomelo",
        "provider_status": raw_status,
        "source": str(
            details.get(
                "servedFrom",
                "UNKNOWN",
            )
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
        return _discord(username)

    if platform == "Roblox":
        return _roblox(username)

    if platform == "Minecraft":
        return _minecraft_batch(
            [username]
        )[0]

    return {
        **result(
            username,
            "unknown",
            "format_valid_but_availability_unverified",
        ),
        "cached": False,
    }


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

        name = value.strip()

        if (
            name
            and name.lower() not in seen
        ):
            unique.append(name)
            seen.add(name.lower())

    batch = unique[:MAX_BATCH_SIZE]
    results = []

    if platform == "Minecraft":
        pending = []

        for username in batch:
            valid, why = validate_username(
                platform,
                username,
            )

            if not valid:
                results.append(
                    result(
                        username,
                        "invalid",
                        why,
                    )
                )

            else:
                cached = _cache_get(
                    platform,
                    username,
                )

                if cached:
                    results.append(cached)

                else:
                    pending.append(
                        username
                    )

        resolved = {}

        for index in range(
            0,
            len(pending),
            MINECRAFT_BULK_SIZE,
        ):
            group = pending[
                index:
                index + MINECRAFT_BULK_SIZE
            ]

            answer = _minecraft_batch(
                group
            )

            for item in answer:
                resolved[
                    item["username"].lower()
                ] = item

            if any(
                item["reason"] == "rate_limited"
                for item in answer
            ):
                for name in pending[
                    index + len(group):
                ]:
                    resolved[name.lower()] = (
                        result(
                            name,
                            "unknown",
                            "rate_limited",
                        )
                    )

                break

        known = {
            item["username"].lower(): item
            for item in results
        }

        known.update(resolved)

        results = [
            known[name.lower()]
            for name in batch
        ]

    elif platform == "Discord":
        attempts = 0

        for i, name in enumerate(batch):
            name = name.lower()

            valid, why = validate_username(
                platform,
                name,
            )

            if not valid:
                results.append(
                    result(
                        name,
                        "invalid",
                        why,
                    )
                )
                continue

            cached = _cache_get(
                platform,
                name,
            )

            if cached:
                results.append(cached)
                continue

            if attempts >= DISCORD_MAX_PER_BATCH:
                results.append(
                    result(
                        name,
                        "unknown",
                        "discord_batch_limit",
                    )
                )
                continue

            checked = check_username(
                platform,
                name,
            )

            attempts += 1
            results.append(checked)

            if checked.get("reason") in (
                "discord_rate_limited",
                "discord_access_blocked",
            ):
                for rest in batch[
                    i + 1:
                ]:
                    results.append(
                        result(
                            rest.lower(),
                            "unknown",
                            "discord_paused",
                        )
                    )

                break

    elif platform == "Roblox":
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
