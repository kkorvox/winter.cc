
import json
import threading
import time
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

_lock = threading.Lock()
_cache = {}
_next_request = {}
_cooldown = {}


def result(username, status="unknown", reason=""):
    return {
        "username": username,
        "status": status,
        "reason": reason,
    }


def _request_json(platform, url):
    with _lock:
        current = time.monotonic()

        if current < _cooldown.get(platform, 0):
            return None, "rate_limited"

        wait = max(
            0,
            _next_request.get(platform, 0) - current
        )

        if wait:
            time.sleep(wait)

        _next_request[platform] = (
            time.monotonic() + MIN_REQUEST_INTERVAL
        )

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "winter.cc/4.0"
            }
        )

        try:
            with urlopen(request, timeout=8) as response:
                return json.load(response), None

        except HTTPError as error:
            if error.code == 429:
                retry_after = error.headers.get(
                    "Retry-After", "60"
                )

                try:
                    delay = max(
                        5,
                        min(float(retry_after), 300)
                    )
                except ValueError:
                    delay = 60

                _cooldown[platform] = (
                    time.monotonic() + delay
                )

                return None, "rate_limited"

            if error.code == 404:
                return None, "not_found"

            return None, "http_error"

        except (
            URLError,
            TimeoutError,
            ValueError,
            OSError
        ):
            return None, "network_error"


def _check_roblox(username):
    # Roblox signup username validation.
    # Code 0 means valid for signup at check time.
    # Other codes are not automatically "taken".
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
        return "available", "roblox_validation"

    # Roblox code 1 indicates username already in use.
    if code == 1:
        return "claimed", "roblox_validation"

    return "unknown", "roblox_rejected_or_invalid"


def _check_minecraft(username):
    # Minecraft profile lookup identifies existing names.
    # A 404 does NOT prove a name is available.
    url = (
        "https://api.mojang.com/users/profiles/"
        "minecraft/" + quote(username, safe="")
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
        and data.get("name", "").lower()
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
            username, "unknown", "empty_username"
        )

    key = (platform, username.lower())
    current = time.monotonic()

    with _lock:
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

    checked = result(username, status, reason)
    checked["cached"] = False

    if reason not in (
        "rate_limited",
        "network_error",
        "http_error",
        "invalid_response"
    ):
        with _lock:
            _cache[key] = (
                checked,
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

    checked = []

    for username in unique[:MAX_BATCH_SIZE]:
        item = check_username(
            platform, username
        )

        checked.append(item)

        if item["reason"] == "rate_limited":
            break

    return {
        "platform": platform,
        "results": checked,
        "checked": len(checked),
        "total": len(unique),
        "remaining": len(unique) - len(checked),
    }
