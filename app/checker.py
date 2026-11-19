
import json
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

        seconds = (
            date - datetime.now(timezone.utc)
        ).total_seconds()

        return max(5.0, min(seconds, 300.0))

    except (TypeError, ValueError, OverflowError):
        return 60.0


def _wait_for_request_slot(platform):
    while True:
        with _schedule_lock:
            current = time.monotonic()

            cooldown_until = _cooldown.get(
                platform, 0
            )

            if current < cooldown_until:
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
                "User-Agent": "winter.cc/5.0"
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

            if error.code in (500, 502, 503, 504):
                if attempt < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                return None, "http_error"

            return None, "http_error"

        except (
            URLError,
            TimeoutError,
            OSError
        ):
            if attempt < MAX_RETRIES:
                time.sleep(0.5 * (attempt + 1))
                continue

            return None, "network_error"

        except (ValueError, UnicodeError):
            return None, "invalid_response"

    return None, "network_error"


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

    data, error = _request_json(
        "Roblox", url
    )

    if error:
        return "unknown", error

    if not isinstance(data, dict):
        return "unknown", "invalid_response"

    code = data.get("code")

    if code == 0:
        return "available", "roblox_validation"

    if code == 1:
        return "claimed", "roblox_validation"

    return "unknown", "roblox_rejected_or_invalid"


def _check_minecraft(username):
    url = (
        "https://api.mojang.com/users/profiles/"
        "minecraft/" +
        quote(username, safe="")
    )

    data, error = _request_json(
        "Minecraft", url
    )

    if error == "not_found":
        return "unknown", "no_existing_profile"

    if error:
        return "unknown", error

    if (
        isinstance(data, dict)
        and data.get("id")
        and str(data.get("name", "")).lower()
        == username.lower()
    ):
        return "claimed", "existing_profile"

    return "unknown", "inconclusive"


def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")

    username = str(username).strip()

    if not username:
        return result(
            username,
            "unknown",
            "empty_username"
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
        status, reason = _check_roblox(username)

    elif platform == "Minecraft":
        status, reason = _check_minecraft(username)

    else:
        status = "unknown"
        reason = "no_supported_availability_api"

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

    # Unsupported platforms do not need network requests.
    if platform not in ("Roblox", "Minecraft"):
        results = [
            result(
                username,
                "unknown",
                "no_supported_availability_api"
            )
            for username in batch
        ]

    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_WORKERS, len(batch))
        ) as executor:
            results = list(
                executor.map(
                    lambda username: check_username(
                        platform, username
                    ),
                    batch
                )
            )

    return {
        "platform": platform,
        "results": results,
        "checked": len(results),
        "total": len(unique),
        "remaining": len(unique) - len(results),
    }
