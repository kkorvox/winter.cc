
# winter.cc Username Checker V6.5
# Full replacement for app/checker.py
#
# Discord: Pomelo third-party API
# Roblox: signup username validation
# Minecraft: existing profile lookup
# Instagram/TikTok/Telegram: format validation only
#
# Pomelo is unofficial. Its results are reports,
# not guarantees that Discord will accept a username.

import json
import re
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

CACHE_SECONDS = 300
MIN_REQUEST_INTERVAL = 1.5
MAX_BATCH_SIZE = 25
MAX_WORKERS = 4
REQUEST_TIMEOUT = 8
MAX_RETRIES = 2

# Free-tier pacing: one request per 5.5 seconds.
POMELO_INTERVAL = 5.5

# Avoid spending too long on a single HTTP request.
# Your frontend already sends Discord names one at a time.
POMELO_MAX_PER_BATCH = 4

_cache_lock = threading.Lock()
_schedule_lock = threading.Lock()
_pomelo_lock = threading.Lock()

_cache = {}
_next_request = {}
_cooldown = {}

_pomelo_next_request = 0.0
_pomelo_cooldown_until = 0.0


def result(username, status="unknown", reason=""):
    return {
        "username": username,
        "status": status,
        "reason": reason,
    }


# =====================================
# USERNAME VALIDATION
# =====================================

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

        if not re.fullmatch(
            r"[a-z0-9._]+",
            username
        ):
            return False, "discord_invalid_characters"

        if ".." in username:
            return False, "discord_consecutive_periods"

        if username.startswith("."):
            return False, "discord_invalid_period_position"

        if username.endswith("."):
            return False, "discord_invalid_period_position"

        return True, "discord_format_valid"

    if platform == "Roblox":
        valid = (
            3 <= len(username) <= 20
            and bool(re.fullmatch(
                r"[A-Za-z0-9_]+",
                username
            ))
            and not username.startswith("_")
            and not username.endswith("_")
            and username.count("_") <= 1
        )

    elif platform == "Instagram":
        valid = (
            1 <= len(username) <= 30
            and bool(re.fullmatch(
                r"[A-Za-z0-9._]+",
                username
            ))
            and not username.endswith(".")
        )

    elif platform == "TikTok":
        valid = (
            2 <= len(username) <= 24
            and bool(re.fullmatch(
                r"[A-Za-z0-9._]+",
                username
            ))
            and not username.endswith(".")
        )

    elif platform == "Telegram":
        valid = (
            5 <= len(username) <= 32
            and bool(re.fullmatch(
                r"[A-Za-z0-9_]+",
                username
            ))
            and bool(re.match(
                r"[A-Za-z]",
                username
            ))
        )

    elif platform == "Minecraft":
        valid = (
            3 <= len(username) <= 16
            and bool(re.fullmatch(
                r"[A-Za-z0-9_]+",
                username
            ))
        )

    else:
        valid = False

    if not valid:
        return False, "invalid_format"

    return True, "format_valid"


# =====================================
# NETWORK HELPERS
# =====================================

def _retry_after_seconds(value):
    if not value:
        return 60.0

    try:
        return max(
            5.0,
            min(float(value), 300.0)
        )
    except (TypeError, ValueError):
        pass

    try:
        date = parsedate_to_datetime(value)

        if date.tzinfo is None:
            date = date.replace(
                tzinfo=timezone.utc
            )

        delay = (
            date - datetime.now(timezone.utc)
        ).total_seconds()

        return max(
            5.0,
            min(delay, 300.0)
        )

    except (
        TypeError,
        ValueError,
        OverflowError
    ):
        return 60.0


def _wait_for_request_slot(platform):
    while True:
        with _schedule_lock:
            current = time.monotonic()

            if current < _cooldown.get(
                platform, 0
            ):
                return False

            next_allowed = _next_request.get(
                platform, 0
            )

            if current >= next_allowed:
                _next_request[platform] = (
                    current + MIN_REQUEST_INTERVAL
                )
                return True

            wait = next_allowed - current

        time.sleep(min(wait, 0.25))


def _request_json(platform, url):
    for attempt in range(MAX_RETRIES + 1):
        if not _wait_for_request_slot(platform):
            return None, "rate_limited"

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "winter.cc/6.5",
            },
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
                    error.headers.get(
                        "Retry-After"
                    )
                )

                with _schedule_lock:
                    _cooldown[platform] = max(
                        _cooldown.get(platform, 0),
                        time.monotonic() + delay
                    )

                return None, "rate_limited"

            if error.code == 404:
                return None, "not_found"

            if error.code in (
                500, 502, 503, 504
            ):
                if attempt < MAX_RETRIES:
                    time.sleep(
                        0.5 * (attempt + 1)
                    )
                    continue

            return None, "http_error"

        except (
            URLError,
            TimeoutError,
            OSError
        ):
            if attempt < MAX_RETRIES:
                time.sleep(
                    0.5 * (attempt + 1)
                )
                continue

            return None, "network_error"

        except (
            ValueError,
            UnicodeError
        ):
            return None, "invalid_response"

    return None, "network_error"


# =====================================
# ROBLOX CHECKER
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

    data, error = _request_json(
        "Roblox",
        url
    )

    if error:
        return "unknown", error

    if not isinstance(data, dict):
        return "unknown", "invalid_response"

    code = data.get("code")

    if code == 0:
        return (
            "available",
            "roblox_signup_validation"
        )

    if code == 1:
        return (
            "claimed",
            "roblox_signup_validation"
        )

    return (
        "unknown",
        "roblox_rejected_or_inconclusive"
    )


# =====================================
# MINECRAFT CHECKER
# =====================================

def _check_minecraft(username):
    url = (
        "https://api.mojang.com"
        "/users/profiles/minecraft/"
        + quote(username, safe="")
    )

    data, error = _request_json(
        "Minecraft",
        url
    )

    if error == "not_found":
        return (
            "unknown",
            "no_existing_profile"
        )

    if error:
        return "unknown", error

    if (
        isinstance(data, dict)
        and data.get("id")
        and str(
            data.get("name", "")
        ).lower() == username.lower()
    ):
        return (
            "claimed",
            "existing_profile"
        )

    return "unknown", "inconclusive"


# =====================================
# POMELO REQUEST SCHEDULING
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


# =====================================
# DISCORD / POMELO CHECKER
# =====================================

def test_pomelo_username(username):
    """
    Check one username through Pomelo.

    This function is used by both:
      - Automatic Discord checking
      - The owner-only Pomelo test endpoint

    Returns provider information and
    a reason for any unsuccessful lookup.
    """

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
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/6.5",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT
        ) as response:
            data = json.load(response)

    except HTTPError as error:
        if error.code == 429:
            delay = _retry_after_seconds(
                error.headers.get(
                    "Retry-After"
                )
            )

            _pomelo_set_cooldown(delay)
            reason = "pomelo_rate_limited"

        elif error.code in (401, 403):
            reason = "pomelo_access_denied"

        else:
            reason = (
                f"pomelo_http_{error.code}"
            )

        return _pomelo_unknown(
            username,
            reason
        )

    except (
        URLError,
        TimeoutError,
        OSError
    ):
        return _pomelo_unknown(
            username,
            "pomelo_network_error"
        )

    except (
        ValueError,
        UnicodeError
    ):
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
        details.get(
            "servedFrom",
            "UNKNOWN"
        )
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

    return {
        "username": username,
        "status": status,
        "reason": reason,
        "provider": "Pomelo",
        "provider_status": reported_status,
        "source": source,
        "duration": data.get("duration"),
    }


def _check_discord(username):
    """
    Automatic Discord availability checking.
    """

    data = test_pomelo_username(
        username
    )

    return (
        data["status"],
        data["reason"]
    )


# =====================================
# STANDARD CHECKER
# =====================================

def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            "Unsupported platform"
        )

    username = str(username).strip()

    if platform == "Discord":
        username = username.lower()

    valid, validation_reason = (
        validate_username(
            platform,
            username
        )
    )

    if not valid:
        return result(
            username,
            "invalid",
            validation_reason
        )

    key = (
        platform,
        username.lower()
    )

    current = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)

        if cached and cached[1] > current:
            saved = dict(cached[0])
            saved["cached"] = True
            return saved

    if platform == "Discord":
        checked = test_pomelo_username(
            username
        )

    elif platform == "Roblox":
        status, reason = _check_roblox(
            username
        )

        checked = result(
            username,
            status,
            reason
        )

    elif platform == "Minecraft":
        status, reason = _check_minecraft(
            username
        )

        checked = result(
            username,
            status,
            reason
        )

    else:
        checked = result(
            username,
            "unknown",
            "format_valid_but_availability_unverified"
        )

    checked["cached"] = False

    temporary_errors = {
        "rate_limited",
        "network_error",
        "http_error",
        "invalid_response",
        "pomelo_cooldown",
        "pomelo_rate_limited",
        "pomelo_access_denied",
        "pomelo_network_error",
        "pomelo_invalid_response",
        "pomelo_missing_data",
        "pomelo_missing_status",
        "pomelo_unrecognized_status",
    }

    reason = checked.get(
        "reason",
        ""
    )

    is_temporary = (
        reason in temporary_errors
        or reason.startswith(
            "pomelo_http_"
        )
    )

    if not is_temporary:
        with _cache_lock:
            _cache[key] = (
                dict(checked),
                time.monotonic()
                + CACHE_SECONDS
            )

    return checked


# =====================================
# BATCH CHECKING
# =====================================

def check_batch(platform, usernames):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            "Unsupported platform"
        )

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

        attempted = 0

        for username in batch:
            valid, reason = validate_username(
                platform,
                username
            )

            if not valid:
                results.append(
                    result(
                        username,
                        "invalid",
                        reason
                    )
                )
                continue

            key = (
                platform,
                username.lower()
            )

            with _cache_lock:
                cached = _cache.get(key)

                if (
                    cached
                    and cached[1] > time.monotonic()
                ):
                    saved = dict(cached[0])
                    saved["cached"] = True
                else:
                    saved = None

            if saved is not None:
                results.append(saved)
                continue

            if attempted >= POMELO_MAX_PER_BATCH:
                results.append(
                    result(
                        username,
                        "unknown",
                        "pomelo_batch_limit"
                    )
                )
                continue

            checked = check_username(
                platform,
                username
            )

            results.append(checked)
            attempted += 1

    elif platform in NETWORK_CHECKERS:
        with ThreadPoolExecutor(
            max_workers=min(
                MAX_WORKERS,
                len(batch)
            )
        ) as executor:
            results = list(
                executor.map(
                    lambda name: check_username(
                        platform,
                        name
                    ),
                    batch
                )
            )

    else:
        results = [
            check_username(
                platform,
                name
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
