
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
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

CACHE_SECONDS = 300
MIN_REQUEST_INTERVAL = 1.5
MAX_BATCH_SIZE = 25
MAX_WORKERS = 4
REQUEST_TIMEOUT = 6
MAX_RETRIES = 2

_cache_lock = threading.Lock()
_schedule_lock = threading.Lock()

_cache = {}
_next_request = {}
_cooldown = {}


def result(username, status="unknown", reason=""):
    return {
        "username": username,
        "status": status,
        "reason": reason,
    }


def validate_username(platform, username):
    """
    Conservative local validation.

    True: passes the rules checked here.
    False: violates a known formatting rule.

    Passing does not prove that a username is
    accepted or available for registration.
    """

    if platform not in SUPPORTED_PLATFORMS:
        return False, "unsupported_platform"

    if not username:
        return False, "empty_username"

    if platform == "Discord":
        valid = (
            2 <= len(username) <= 32
            and bool(re.fullmatch(
                r"[a-z0-9._]+",
                username
            ))
            and ".." not in username
        )

    elif platform == "Roblox":
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
            and username[0].isalpha()
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

        return max(5.0, min(delay, 300.0))

    except (TypeError, ValueError, OverflowError):
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
                "User-Agent": "winter.cc/5.1"
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

        except (ValueError, UnicodeError):
            return None, "invalid_response"

    return None, "network_error"


def _check_roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday":
            "2000-01-01T00:00:00Z",
        "request.context": "Signup",
    })

    url = (
        "https://auth.roblox.com"
        "/v1/usernames/validate?"
        + params
    )

    data, error = _request_json(
        "Roblox", url
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


def _check_minecraft(username):
    url = (
        "https://api.mojang.com/users/profiles/"
        "minecraft/"
        + quote(username, safe="")
    )

    data, error = _request_json(
        "Minecraft", url
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
        return "claimed", "existing_profile"

    return "unknown", "inconclusive"


def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")

    username = str(username).strip()

    valid, validation_reason = (
        validate_username(platform, username)
    )

    if not valid:
        return result(
            username,
            "invalid",
            validation_reason
        )

    key = (platform, username.lower())
    current = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)

        if cached and cached[1] > current:
            saved = dict(cached[0])
            saved["cached"] = True
            return saved

    if platform == "Roblox":
        status, reason = _check_roblox(
            username
        )

    elif platform == "Minecraft":
        status, reason = _check_minecraft(
            username
        )

    else:
        status = "unknown"
        reason = (
            "format_valid_but_availability_unverified"
        )

    checked = result(
        username,
        status,
        reason
    )

    checked["cached"] = False

    temporary_errors = {
        "rate_limited",
        "network_error",
        "http_error",
        "invalid_response",
    }

    if reason not in temporary_errors:
        with _cache_lock:
            _cache[key] = (
                dict(checked),
                time.monotonic() + CACHE_SECONDS
            )

    return checked


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

    if platform not in NETWORK_CHECKERS:
        results = [
            check_username(
                platform, username
            )
            for username in batch
        ]

    else:
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

    return {
        "platform": platform,
        "results": results,
        "checked": len(results),
        "total": len(unique),
        "remaining": (
            len(unique) - len(results)
        ),
    }
