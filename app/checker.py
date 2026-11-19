
# winter.cc Username Checker V6.6
# Complete replacement: app/checker.py
#
# Discord: Pomelo third-party API
# Roblox: signup validation
# Minecraft: existing profile lookup
# Instagram, TikTok, Telegram: format validation
#
# Third-party availability reports are not
# official guarantees that a name can be claimed.

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

NETWORK_CHECKERS = (
    "Roblox",
    "Minecraft",
)

POMELO_URL = (
    "https://api.pomelo.lixqa.cc/v1/lookups"
)

MAX_BATCH_SIZE = 25
MAX_WORKERS = 4

CACHE_SECONDS = 300
NEGATIVE_CACHE_SECONDS = 60

MIN_REQUEST_INTERVAL = 1.5

# Pomelo's documented free-tier pacing:
# no more than two requests per ten seconds.
# Use a small safety margin.
POMELO_INTERVAL = 5.5

# The existing frontend sends one Discord name
# per request. Limit larger direct batch calls.
POMELO_MAX_PER_BATCH = 4

REQUEST_TIMEOUT = 7
POMELO_TIMEOUT = 10
MAX_RETRIES = 1

_cache_lock = threading.Lock()
_schedule_lock = threading.Lock()
_pomelo_lock = threading.Lock()

_cache = {}
_next_request = {}
_cooldown = {}

_pomelo_next_request = 0.0
_pomelo_cooldown_until = 0.0


# =====================================
# BASIC RESULTS AND VALIDATION
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
        if not 2 <= len(username) <= 32:
            return False, "discord_invalid_length"

        if not re.fullmatch(r"[a-z0-9._]+", username):
            return False, "discord_invalid_characters"

        if ".." in username:
            return False, "discord_consecutive_periods"

        if username.startswith(".") or username.endswith("."):
            return False, "discord_invalid_period_position"

        return True, "discord_format_valid"

    if platform == "Roblox":
        valid = (
            3 <= len(username) <= 20
            and bool(re.fullmatch(r"[A-Za-z0-9_]+", username))
            and not username.startswith("_")
            and not username.endswith("_")
            and username.count("_") <= 1
        )

    elif platform == "Instagram":
        valid = (
            1 <= len(username) <= 30
            and bool(re.fullmatch(r"[A-Za-z0-9._]+", username))
            and not username.endswith(".")
        )

    elif platform == "TikTok":
        valid = (
            2 <= len(username) <= 24
            and bool(re.fullmatch(r"[A-Za-z0-9._]+", username))
            and not username.endswith(".")
        )

    elif platform == "Telegram":
        valid = (
            5 <= len(username) <= 32
            and bool(re.fullmatch(r"[A-Za-z0-9_]+", username))
            and bool(re.match(r"[A-Za-z]", username))
        )

    elif platform == "Minecraft":
        valid = (
            3 <= len(username) <= 16
            and bool(re.fullmatch(r"[A-Za-z0-9_]+", username))
        )

    else:
        valid = False

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
        item = _cache.get(key)

        if not item:
            return None

        saved, expires = item

        if expires <= time.monotonic():
            del _cache[key]
            return None

        response = dict(saved)
        response["cached"] = True
        return response


def _cache_set(platform, username, data):
    status = data.get("status", "unknown")
    reason = data.get("reason", "")

    transient = {
        "rate_limited",
        "network_error",
        "http_error",
        "invalid_response",
        "pomelo_cooldown",
        "pomelo_rate_limited",
        "pomelo_dns_error",
        "pomelo_ssl_error",
        "pomelo_timeout",
        "pomelo_connection_refused",
        "pomelo_network_error",
        "pomelo_access_denied",
        "pomelo_invalid_response",
        "pomelo_missing_data",
        "pomelo_missing_status",
        "pomelo_unrecognized_status",
        "pomelo_batch_limit",
    }

    if (
        reason in transient
        or reason.startswith("pomelo_http_")
    ):
        return

    ttl = (
        CACHE_SECONDS
        if status in ("available", "claimed", "invalid")
        else NEGATIVE_CACHE_SECONDS
    )

    key = (platform, username.lower())

    with _cache_lock:
        _cache[key] = (
            dict(data),
            time.monotonic() + ttl
        )


# =====================================
# NETWORK HELPERS
# =====================================

def _retry_after_seconds(value):
    if not value:
        return 60.0

    try:
        return max(5.0, min(float(value), 300.0))
    except (TypeError, ValueError):
        pass

    try:
        date = parsedate_to_datetime(value)

        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)

        delay = (
            date - datetime.now(timezone.utc)
        ).total_seconds()

        return max(5.0, min(delay, 300.0))

    except (TypeError, ValueError, OverflowError):
        return 60.0


def _wait_for_request_slot(platform):
    while True:
        with _schedule_lock:
            current = time.monotonic()

            if current < _cooldown.get(platform, 0):
                return False

            next_allowed = _next_request.get(platform, 0)

            if current >= next_allowed:
                _next_request[platform] = (
                    current + MIN_REQUEST_INTERVAL
                )
                return True

            delay = next_allowed - current

        time.sleep(min(delay, 0.25))


def _request_json(platform, url):
    for attempt in range(MAX_RETRIES + 1):
        if not _wait_for_request_slot(platform):
            return None, "rate_limited"

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "winter.cc/6.6",
            }
        )

        try:
            with urlopen(
                request,
                timeout=REQUEST_TIMEOUT
            ) as response:
                return json.load(response), None

        except HTTPError as error:
            if error.code == 429:
                delay = _retry_after_seconds(
                    error.headers.get("Retry-After")
                )

                with _schedule_lock:
                    _cooldown[platform] = max(
                        _cooldown.get(platform, 0),
                        time.monotonic() + delay
                    )

                return None, "rate_limited"

            if error.code == 404:
                return None, "not_found"

            if (
                error.code in (500, 502, 503, 504)
                and attempt < MAX_RETRIES
            ):
                time.sleep(0.5)
                continue

            return None, "http_error"

        except (URLError, TimeoutError, OSError):
            if attempt < MAX_RETRIES:
                time.sleep(0.5)
                continue

            return None, "network_error"

        except (ValueError, UnicodeError):
            return None, "invalid_response"

    return None, "network_error"


# =====================================
# ROBLOX
# =====================================

def _check_roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday": "2000-01-01T00:00:00Z",
        "request.context": "Signup",
    })

    url = (
        "https://auth.roblox.com"
        "/v1/usernames/validate?"
        + params
    )

    data, error = _request_json("Roblox", url)

    if error:
        return "unknown", error

    if not isinstance(data, dict):
        return "unknown", "invalid_response"

    code = data.get("code")

    if code == 0:
        return "available", "roblox_signup_validation"

    if code == 1:
        return "claimed", "roblox_signup_validation"

    return "unknown", "roblox_rejected_or_inconclusive"


# =====================================
# MINECRAFT
# =====================================

def _check_minecraft(username):
    url = (
        "https://api.mojang.com"
        "/users/profiles/minecraft/"
        + quote(username, safe="")
    )

    data, error = _request_json("Minecraft", url)

    if error == "not_found":
        return "unknown", "no_existing_profile"

    if error:
        return "unknown", error

    if (
        isinstance(data, dict)
        and data.get("id")
        and str(data.get("name", "")).lower() == username.lower()
    ):
        return "claimed", "existing_profile"

    return "unknown", "inconclusive"


# =====================================
# POMELO PACING
# =====================================

def _pomelo_request_slot():
    global _pomelo_next_request

    with _pomelo_lock:
        current = time.monotonic()

        if current < _pomelo_cooldown_until:
            return False

        if current < _pomelo_next_request:
            return False

        _pomelo_next_request = (
            current + POMELO_INTERVAL
        )

        return True


def _pomelo_set_cooldown(seconds):
    global _pomelo_cooldown_until

    with _pomelo_lock:
        _pomelo_cooldown_until = max(
            _pomelo_cooldown_until,
            time.monotonic() + seconds
        )


def _pomelo_unknown(username, reason):
    return {
        "username": username,
        "status": "unknown",
        "reason": reason,
        "provider": "Pomelo",
        "source": None,
    }


def _pomelo_network_reason(error):
    """
    Categorize errors without exposing sensitive
    server details to the frontend.
    """

    cause = error

    if isinstance(error, URLError):
        cause = error.reason

    if isinstance(cause, ssl.SSLError):
        return "pomelo_ssl_error"

    if isinstance(cause, socket.gaierror):
        return "pomelo_dns_error"

    if isinstance(cause, (socket.timeout, TimeoutError)):
        return "pomelo_timeout"

    if isinstance(cause, ConnectionRefusedError):
        return "pomelo_connection_refused"

    if isinstance(cause, OSError):
        if cause.errno in (101, 113):
            return "pomelo_network_error"

        if cause.errno == 111:
            return "pomelo_connection_refused"

    return "pomelo_network_error"


# =====================================
# DISCORD / POMELO
# =====================================

def test_pomelo_username(username):
    username = str(username).strip().lower()

    valid, reason = validate_username(
        "Discord",
        username
    )

    if not valid:
        return {
            "username": username,
            "status": "invalid",
            "reason": reason,
            "provider": "local_validation",
            "source": None,
        }

    cached = _cache_get("Discord", username)

    if cached:
        return cached

    if not _pomelo_request_slot():
        return _pomelo_unknown(
            username,
            "pomelo_cooldown"
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
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/6.6",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=POMELO_TIMEOUT
        ) as response:
            data = json.load(response)

    except HTTPError as error:
        if error.code == 429:
            delay = _retry_after_seconds(
                error.headers.get("Retry-After")
            )

            _pomelo_set_cooldown(delay)
            reason = "pomelo_rate_limited"

        elif error.code in (401, 403):
            reason = "pomelo_access_denied"

        else:
            reason = f"pomelo_http_{error.code}"

        return _pomelo_unknown(username, reason)

    except (URLError, TimeoutError, OSError) as error:
        return _pomelo_unknown(
            username,
            _pomelo_network_reason(error)
        )

    except (ValueError, UnicodeError):
        return _pomelo_unknown(
            username,
            "pomelo_invalid_response"
        )

    if not isinstance(data, dict):
        return _pomelo_unknown(
            username,
            "pomelo_invalid_response"
        )

    details = data.get("data")

    if not isinstance(details, dict):
        return _pomelo_unknown(
            username,
            "pomelo_missing_data"
        )

    reported_status = str(
        details.get("status", "")
    ).upper()

    source = str(
        details.get("servedFrom", "UNKNOWN")
    ).upper()

    status_map = {
        "AVAILABLE": "available",
        "TAKEN": "claimed",
        "RESERVED": "claimed",
    }

    status = status_map.get(
        reported_status,
        "unknown"
    )

    if status == "unknown":
        reason = (
            "pomelo_unrecognized_status"
            if reported_status
            else "pomelo_missing_status"
        )
    else:
        reason = (
            "pomelo_reported_" +
            reported_status.lower()
        )

    checked = {
        "username": username,
        "status": status,
        "reason": reason,
        "provider": "Pomelo",
        "provider_status": reported_status,
        "source": source,
        "duration": data.get("duration"),
        "cached": False,
    }

    _cache_set("Discord", username, checked)

    return checked


def _check_discord(username):
    data = test_pomelo_username(username)

    return (
        data["status"],
        data["reason"]
    )


# =====================================
# SINGLE USERNAME CHECK
# =====================================

def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")

    username = str(username).strip()

    if platform == "Discord":
        username = username.lower()

    valid, validation_reason = validate_username(
        platform,
        username
    )

    if not valid:
        return result(
            username,
            "invalid",
            validation_reason
        )

    cached = _cache_get(platform, username)

    if cached:
        return cached

    if platform == "Discord":
        return test_pomelo_username(username)

    if platform == "Roblox":
        status, reason = _check_roblox(username)

    elif platform == "Minecraft":
        status, reason = _check_minecraft(username)

    else:
        status = "unknown"
        reason = "format_valid_but_availability_unverified"

    checked = result(username, status, reason)
    checked["cached"] = False

    _cache_set(platform, username, checked)

    return checked


# =====================================
# BATCH CHECKING
# =====================================

def check_batch(platform, usernames):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")

    unique = list(dict.fromkeys(
        str(name).strip()
        for name in usernames
        if str(name).strip()
    ))

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
        attempts = 0

        for username in batch:
            valid, reason = validate_username(
                platform,
                username.lower()
            )

            if not valid:
                results.append(
                    result(username, "invalid", reason)
                )
                continue

            cached = _cache_get(
                "Discord",
                username
            )

            if cached:
                results.append(cached)
                continue

            if attempts >= POMELO_MAX_PER_BATCH:
                results.append(
                    result(
                        username,
                        "unknown",
                        "pomelo_batch_limit"
                    )
                )
                continue

            results.append(
                check_username(platform, username)
            )

            attempts += 1

    elif platform in NETWORK_CHECKERS:
        with ThreadPoolExecutor(
            max_workers=min(
                MAX_WORKERS,
                len(batch)
            )
        ) as executor:
            results = list(
                executor.map(
                    lambda username: check_username(
                        platform,
                        username
                    ),
                    batch
                )
            )

    else:
        results = [
            check_username(platform, username)
            for username in batch
        ]

    return {
        "platform": platform,
        "results": results,
        "checked": len(results),
        "total": len(unique),
        "remaining": len(unique) - len(results),
    }
