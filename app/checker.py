
"""winter.cc Checker V7.0 - working Discord checker.

Complete replacement for app/checker.py.
Discord uses its direct username eligibility endpoint.
Pomelo remains for Owner Dashboard diagnostics only.
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
    "Discord", "Roblox", "Instagram",
    "TikTok", "Telegram", "Minecraft",
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
POMELO_MIN_INTERVAL = 5.5
DISCORD_MAX_PER_BATCH = 4
MAX_CACHE_ENTRIES = 2000

CACHE_SECONDS = {
    "available": 15,
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

        if re.fullmatch(r"[a-z0-9._]+", name) is None:
            return False, "discord_invalid_characters"

        if ".." in name:
            return False, "discord_consecutive_periods"

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


def _cache_get(platform, username):
    with _cache_lock:
        key = (platform, username.lower())
        entry = _cache.get(key)

        if entry is None:
            return None

        checked, expires = entry

        if time.monotonic() >= expires:
            _cache.pop(key, None)
            return None

        return {
            **checked,
            "cached": True,
        }


def _cache_set(platform, username, checked):
    ttl = CACHE_SECONDS.get(
        checked.get("status")
    )

    if ttl is None:
        return

    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            now = time.monotonic()

            for key in list(_cache):
                if _cache[key][1] <= now:
                    _cache.pop(key, None)

            while len(_cache) >= MAX_CACHE_ENTRIES:
                _cache.pop(next(iter(_cache)))

        _cache[
            (platform, username.lower())
        ] = (
            dict(checked),
            time.monotonic() + ttl,
        )


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
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return max(
            1.0,
            min(
                (
                    dt - datetime.now(timezone.utc)
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


def _get_json(platform, url):
    with _general_lock:
        now = time.monotonic()

        if now < _general_cooldown.get(platform, 0):
            return None, "rate_limited"

        wait = max(
            0.0,
            _general_next.get(platform, 0) - now,
        )

        _general_next[platform] = (
            max(
                now,
                _general_next.get(platform, 0),
            ) + 1.5
        )

    if wait:
        time.sleep(wait)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "winter.cc/7.0",
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
            delay = _retry_after(
                error.headers.get("Retry-After")
            )

            with _general_lock:
                _general_cooldown[platform] = max(
                    _general_cooldown.get(platform, 0),
                    time.monotonic() + delay,
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


def _check_roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday":
            "2000-01-01T00:00:00Z",
        "request.context": "Signup",
    })

    data, error = _get_json(
        "Roblox",
        "https://auth.roblox.com"
        "/v1/usernames/validate?"
        + params,
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
        and isinstance(data.get("id"), str)
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


_DISCORD_INVALID_CODES = {
    "BASE_TYPE_BAD_LENGTH",
    "USERNAME_INVALID_CHARACTERS",
    "USERNAME_INVALID_REPEATING_DOTS",
    "USERNAME_INVALID_CONTAINS",
}


def _discord_invalid_code(payload):
    found = set()

    def visit(node, depth=0):
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
                    visit(
                        node[key],
                        depth + 1,
                    )

        elif isinstance(node, list):
            for item in node[:8]:
                visit(
                    item,
                    depth + 1,
                )

    visit(payload)

    return (
        sorted(found)[0]
        if found
        else None
    )


def _discord_block(seconds):
    global _discord_cooldown

    _discord_cooldown = max(
        _discord_cooldown,
        time.monotonic() + seconds,
    )


def _discord_success_headers(headers):
    remaining = headers.get(
        "X-RateLimit-Remaining"
    )

    reset_after = headers.get(
        "X-RateLimit-Reset-After"
    )

    if (
        remaining is None
        or reset_after is None
    ):
        return

    try:
        if int(remaining) <= 0:
            _discord_block(
                max(
                    1.0,
                    min(
                        float(reset_after),
                        3600.0,
                    ),
                )
            )

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        pass


def _check_discord(username):
    global _discord_next

    existing = _cache_get(
        "Discord",
        username,
    )

    if existing:
        return existing

    with _discord_lock:
        existing = _cache_get(
            "Discord",
            username,
        )

        if existing:
            return existing

        if time.monotonic() < _discord_cooldown:
            return result(
                username,
                "unknown",
                "discord_rate_limited",
            )

        wait = max(
            0.0,
            _discord_next - time.monotonic(),
        )

        if wait:
            time.sleep(wait)

        if time.monotonic() < _discord_cooldown:
            return result(
                username,
                "unknown",
                "discord_rate_limited",
            )

        _discord_next = (
            time.monotonic()
            + DISCORD_MIN_INTERVAL
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

                _discord_success_headers(
                    response.headers
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

        except HTTPError as error:
            if error.code == 429:
                seconds = _retry_after(
                    error.headers.get(
                        "Retry-After"
                    )
                )

                try:
                    payload = json.loads(
                        error.read(2048)
                        .decode("utf-8")
                    )

                    if isinstance(payload, dict):
                        seconds = max(
                            seconds,
                            _retry_after(
                                payload.get(
                                    "retry_after"
                                ),
                                0.0,
                            ),
                        )

                except (
                    OSError,
                    ValueError,
                    UnicodeError,
                ):
                    pass

                _discord_block(seconds)

                return result(
                    username,
                    "unknown",
                    "discord_rate_limited",
                )

            if error.code in (401, 403):
                _discord_block(180.0)

                return result(
                    username,
                    "unknown",
                    "discord_access_blocked",
                )

            if error.code == 400:
                try:
                    payload = json.loads(
                        error.read(4096)
                        .decode("utf-8")
                    )

                except (
                    OSError,
                    ValueError,
                    UnicodeError,
                ):
                    payload = {}

                invalid = _discord_invalid_code(
                    payload
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


def test_pomelo_username(username):
    """Owner Dashboard diagnostic only."""
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
            "User-Agent": "winter.cc/7.0",
        },
        method="POST",
    )

    def unknown(reason):
        return {
            "username": username,
            "status": "unknown",
            "reason": reason,
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

    return {
        "username": username,
        "status": status,
        "reason": (
            "pomelo_reported_"
            + provider_status.lower()
            if status != "unknown"
            else "pomelo_unexpected_status"
        ),
        "provider": "Pomelo",
        "provider_status": provider_status,
        "source": str(
            details.get(
                "servedFrom",
                "UNKNOWN",
            )
        ),
        "duration": data.get("duration"),
    }


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
        return _check_discord(username)

    if platform == "Roblox":
        checked = _check_roblox(username)

    elif platform == "Minecraft":
        checked = _check_minecraft(username)

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
        attempted = 0

        for username in batch:
            name = username.lower()

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

            if attempted >= DISCORD_MAX_PER_BATCH:
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

            attempted += 1
            results.append(checked)

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
