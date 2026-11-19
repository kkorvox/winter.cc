"""winter.cc checker V7.9 - respectful Discord cooldowns plus working platform checks.

Replace app/checker.py. Preserve the interface used by app/main.py.
Neither rate limits nor availability guarantees can be removed by a checker.
"""

import json
import math
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

SUPPORTED_PLATFORMS = (
    "Discord", "Roblox", "Instagram", "TikTok", "Telegram", "Minecraft",
)
MAX_BATCH_SIZE = 25
MAX_WORKERS = 4
DISCORD_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
MINECRAFT_BULK_URL = "https://api.mojang.com/profiles/minecraft"
POMELO_URL = "https://api.pomelo.lixqa.cc/v1/lookups"
REQUEST_TIMEOUT = 8
DISCORD_TIMEOUT = 7
POMELO_TIMEOUT = 8
DISCORD_MIN_INTERVAL = 3.0
MINECRAFT_MIN_INTERVAL = 3.0
POMELO_MIN_INTERVAL = 5.5
SOCIAL_TIMEOUT = 5
SOCIAL_INTERVAL = {"Instagram": 2.5, "TikTok": 2.5, "Telegram": 1.5}
_social_locks = {p: threading.RLock() for p in SOCIAL_INTERVAL}
DISCORD_MAX_PER_BATCH = 4  # Safeguard for direct batch requests; website sends 1
MINECRAFT_BULK_SIZE = 10
MAX_CACHE_ENTRIES = 2000
CACHE_SECONDS = {"available": 15, "claimed": 120, "invalid": 300}

_cache = {}
_cache_lock = threading.RLock()
_slots_lock = threading.Lock()
_next_request = {}
_cooldowns = {}
_discord_lock = threading.Lock()
_pomelo_lock = threading.Lock()


def result(username, status="unknown", reason="", **extras):
    return {"username": username, "status": status, "reason": reason, **extras}


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
        if re.fullmatch(r"[a-z0-9._]+", name) is None or ".." in name:
            return False, "discord_invalid_characters"
        return True, "discord_format_valid"
    if platform == "Roblox":
        valid = (3 <= len(username) <= 20 and
                 re.fullmatch(r"[A-Za-z0-9_]+", username) is not None and
                 not username.startswith("_") and not username.endswith("_") and
                 username.count("_") <= 1)
    elif platform == "Instagram":
        valid = (1 <= len(username) <= 30 and
                 re.fullmatch(r"[A-Za-z0-9._]+", username) is not None and
                 not username.endswith("."))
    elif platform == "TikTok":
        valid = (2 <= len(username) <= 24 and
                 re.fullmatch(r"[A-Za-z0-9._]+", username) is not None and
                 not username.endswith("."))
    elif platform == "Telegram":
        valid = (5 <= len(username) <= 32 and
                 re.fullmatch(r"[A-Za-z0-9_]+", username) is not None and
                 re.match(r"[A-Za-z]", username) is not None)
    else:
        valid = (3 <= len(username) <= 16 and
                 re.fullmatch(r"[A-Za-z0-9_]+", username) is not None)
    return (True, "format_valid") if valid else (False, "invalid_format")


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
        return {**value, "cached": True}


def _cache_set(platform, username, checked):
    ttl = CACHE_SECONDS.get(checked.get("status"))
    if ttl is None:
        return
    with _cache_lock:
        now = time.monotonic()
        if len(_cache) >= MAX_CACHE_ENTRIES:
            for key in list(_cache):
                if _cache[key][1] < now:
                    _cache.pop(key, None)
            while len(_cache) >= MAX_CACHE_ENTRIES:
                _cache.pop(next(iter(_cache)))
        _cache[(platform, username.lower())] = (dict(checked), now + ttl)


def _retry_after(value, default=60.0):
    if value is None:
        return default
    try:
        return max(1.0, min(float(value), 3600.0))
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(1.0, min((parsed - datetime.now(timezone.utc)).total_seconds(), 3600.0))
    except (TypeError, ValueError, OverflowError):
        return default


def _network_reason(error, prefix):
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, ssl.SSLError):
        return prefix + "_ssl_error"
    if isinstance(cause, socket.gaierror):
        return prefix + "_dns_error"
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return prefix + "_timeout"
    if isinstance(cause, ConnectionRefusedError):
        return prefix + "_connection_refused"
    return prefix + "_network_error"


def _wait_slot(platform, interval):
    """Reserve one spaced request. Honor any provider-provided cooldown."""
    with _slots_lock:
        now = time.monotonic()
        if now < _cooldowns.get(platform, 0):
            return False
        when = max(now, _next_request.get(platform, 0))
        _next_request[platform] = when + interval
    delay = max(0.0, when - time.monotonic())
    if delay:
        time.sleep(delay)
    with _slots_lock:
        return time.monotonic() >= _cooldowns.get(platform, 0)


def _block(platform, seconds):
    with _slots_lock:
        _cooldowns[platform] = max(
            _cooldowns.get(platform, 0), time.monotonic() + seconds
        )


def _cooldown_remaining(platform):
    """Seconds remaining in a provider's cooldown (local to this worker)."""
    with _slots_lock:
        return max(0, int(math.ceil(
            _cooldowns.get(platform, 0) - time.monotonic()
        )))


def _roblox(username):
    params = urlencode({
        "request.username": username,
        "request.birthday": "2000-01-01T00:00:00Z",
        "request.context": "Signup",
    })
    if not _wait_slot("Roblox", 1.5):
        return result(username, "unknown", "rate_limited")
    request = Request(
        "https://auth.roblox.com/v1/usernames/validate?" + params,
        headers={"Accept": "application/json", "User-Agent": "winter.cc/7.3"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            _block("Roblox", _retry_after(error.headers.get("Retry-After")))
            return result(username, "unknown", "rate_limited")
        return result(username, "unknown", "roblox_http_" + str(error.code))
    except (URLError, TimeoutError, OSError) as error:
        return result(username, "unknown", _network_reason(error, "roblox"))
    except (ValueError, UnicodeError):
        return result(username, "unknown", "roblox_invalid_response")
    if not isinstance(data, dict) or type(data.get("code")) is not int:
        return result(username, "unknown", "roblox_unexpected_response")
    if data["code"] == 0:
        checked = result(username, "available", "roblox_signup_validation")
    elif data["code"] == 1:
        checked = result(username, "claimed", "roblox_signup_validation")
    else:
        checked = result(username, "unknown", "roblox_rejected_or_inconclusive")
    _cache_set("Roblox", username, checked)
    return checked


def _minecraft_batch(usernames):
    """Lookup up to 10 names in one Mojang request; missing names stay Unknown."""
    if not usernames:
        return []
    if len(usernames) > MINECRAFT_BULK_SIZE:
        raise ValueError("Minecraft batch exceeds 10 names")
    if not _wait_slot("Minecraft", MINECRAFT_MIN_INTERVAL):
        return [result(n, "unknown", "rate_limited") for n in usernames]

    request = Request(
        MINECRAFT_BULK_URL,
        data=json.dumps(usernames).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/7.3",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            _block("Minecraft", _retry_after(
                error.headers.get("Retry-After"), default=35.0
            ))
            reason = "rate_limited"
        else:
            reason = "minecraft_http_" + str(error.code)
        return [result(n, "unknown", reason) for n in usernames]
    except (URLError, TimeoutError, OSError) as error:
        reason = _network_reason(error, "minecraft")
        return [result(n, "unknown", reason) for n in usernames]
    except (ValueError, UnicodeError):
        return [result(n, "unknown", "minecraft_invalid_response") for n in usernames]

    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        return [result(n, "unknown", "minecraft_unexpected_response") for n in usernames]

    claimed = set()
    for item in data:
        name, uid = item.get("name"), item.get("id")
        if isinstance(name, str) and isinstance(uid, str) and uid:
            claimed.add(name.lower())

    answers = []
    for username in usernames:
        if username.lower() in claimed:
            checked = result(username, "claimed", "existing_profile")
            _cache_set("Minecraft", username, checked)
        else:
            checked = result(username, "unknown", "no_existing_profile")
        checked["cached"] = False
        answers.append(checked)
    return answers


class PageSignals(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ""
        self.scripts = {}
        self._title = False
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"][:1500]
        elif tag == "title":
            self._title = True
        elif tag == "script":
            script_id = attrs.get("id")
            self._script = script_id if script_id in (
                "__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE") else None
            if self._script:
                self.scripts[self._script] = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag == "script":
            self._script = None

    def handle_data(self, data):
        if self._title:
            self.title += data[:500]
        if self._script and len(self.scripts[self._script]) < 500000:
            self.scripts[self._script] += data


def _public_page(platform, name):
    urls = {
        "Instagram": "https://www.instagram.com/" + quote(name, safe="") + "/",
        "TikTok": "https://www.tiktok.com/@" + quote(name, safe=""),
        "Telegram": "https://t.me/" + quote(name, safe=""),
    }
    with _social_locks[platform]:
        if not _wait_slot(platform, SOCIAL_INTERVAL[platform]):
            return None, "rate_limited"
        request = Request(urls[platform], headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "winter.cc/7.6 (public profile verification)",
        })
        try:
            with urlopen(request, timeout=SOCIAL_TIMEOUT) as r:
                content_type = r.headers.get("Content-Type", "")
                if "html" not in content_type.lower():
                    return None, "unexpected_content_type"
                return r.read(850000).decode("utf-8", "replace"), None
        except HTTPError as e:
            if e.code == 429:
                _block(platform, _retry_after(e.headers.get("Retry-After")))
                return None, "rate_limited"
            if e.code in (401, 403):
                return None, "access_denied"
            if e.code == 404:
                return None, "no_public_profile_availability_unconfirmed"
            return None, "http_" + str(e.code)
        except (URLError, TimeoutError, OSError) as e:
            return None, _network_reason(e, platform)


def _social_check(platform, name):
    html, error = _public_page(platform, name)
    if error:
        return result(name, "unknown", platform.lower() + "_" + error)
    page = PageSignals()
    try:
        page.feed(html)
    except (ValueError, UnicodeError):
        return result(name, "unknown", platform.lower() + "_invalid_page")

    lower = name.lower()
    if platform == "Instagram":
        # Only trust account-specific profile metadata with the exact handle.
        # A generic login screen, 200 status, or 404 is not availability proof.
        marker = re.compile(r"\(@" + re.escape(lower) + r"\)", re.I)
        metadata = [page.meta.get("og:title", ""), page.meta.get("og:description", ""), page.title]
        if any(marker.search(v) and "instagram" in v.lower() for v in metadata):
            return result(name, "claimed", "instagram_public_profile_confirmed", provider="Instagram")
        return result(name, "unknown", "instagram_profile_not_confirmed")

    if platform == "TikTok":
        # TikTok may embed userInfo in page JSON. A 200 alone proves nothing.
        script = page.scripts.get("__UNIVERSAL_DATA_FOR_REHYDRATION__")
        try:
            if script:
                payload = json.loads(script)
                detail = (payload.get("__DEFAULT_SCOPE__", {})
                          .get("webapp.user-detail", {}))
                info = detail.get("userInfo", {}) if isinstance(detail, dict) else {}
                user = info.get("user", {}) if isinstance(info, dict) else {}
                if (isinstance(user, dict) and
                    str(user.get("uniqueId", "")).lower() == lower and
                    bool(user.get("id"))):
                    return result(name, "claimed", "tiktok_public_profile_confirmed", provider="TikTok")
                if isinstance(detail, dict) and detail.get("statusCode") in (10202, 10221):
                    return result(name, "unknown", "tiktok_no_profile_claimability_unconfirmed")
            old = page.scripts.get("SIGI_STATE")
            if old:
                payload = json.loads(old)
                users = payload.get("UserModule", {}).get("users", {})
                if isinstance(users, dict):
                    user = users.get(name, users.get(lower, {}))
                    if (isinstance(user, dict) and
                        str(user.get("uniqueId", "")).lower() == lower and
                        bool(user.get("id"))):
                        return result(name, "claimed", "tiktok_public_profile_confirmed", provider="TikTok")
        except (ValueError, AttributeError, TypeError):
            pass
        return result(name, "unknown", "tiktok_profile_not_confirmed")

    # Telegram public previews do not prove availability. Confirm a populated
    # page structure; a generic contact link is not evidence of an account.
    if ("tgme_page_title" in html and
        any(s in html for s in ("tgme_page_extra", "tgme_page_description", "tgme_page_photo"))):
        return result(name, "claimed", "telegram_public_profile_confirmed", provider="Telegram")
    return result(name, "unknown", "telegram_public_profile_not_confirmed")



def _discord_invalid_code(payload):
    invalid_codes = {
        "BASE_TYPE_BAD_LENGTH", "USERNAME_INVALID_CHARACTERS",
        "USERNAME_INVALID_REPEATING_DOTS", "USERNAME_INVALID_CONTAINS",
    }
    found = set()
    def visit(node, depth=0):
        if depth > 4:
            return
        if isinstance(node, dict):
            value = node.get("code")
            if isinstance(value, str) and value in invalid_codes:
                found.add(value)
            for key in ("errors", "username", "_errors"):
                if key in node:
                    visit(node[key], depth + 1)
        elif isinstance(node, list):
            for value in node[:8]:
                visit(value, depth + 1)
    visit(payload)
    return sorted(found)[0] if found else None


def _discord(username):
    """Direct eligibility lookup, honoring provider-advertised retry limits."""
    with _discord_lock:
        cached = _cache_get("Discord", username)
        if cached:
            return cached

        remaining = _cooldown_remaining("Discord")
        if remaining:
            return result(username, "unknown", "discord_rate_limited",
                          retry_after=remaining)

        if not _wait_slot("Discord", DISCORD_MIN_INTERVAL):
            return result(username, "unknown", "discord_rate_limited",
                          retry_after=max(1, _cooldown_remaining("Discord")))

        request = Request(
            DISCORD_URL,
            data=json.dumps({"username": username}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "winter.cc/7.9",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=DISCORD_TIMEOUT) as response:
                data = json.load(response)
                remaining_header = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset-After")
                if remaining_header is not None and reset is not None:
                    try:
                        if int(remaining_header) <= 0:
                            _block("Discord", _retry_after(reset, default=60))
                    except (TypeError, ValueError, OverflowError):
                        pass
        except HTTPError as error:
            if error.code == 429:
                retry = (error.headers.get("Retry-After") or
                         error.headers.get("X-RateLimit-Reset-After"))
                seconds = _retry_after(retry, default=60.0)
                try:
                    payload = json.loads(error.read(2048).decode("utf-8"))
                    if isinstance(payload, dict) and payload.get("retry_after") is not None:
                        seconds = max(seconds, _retry_after(
                            payload["retry_after"], default=60.0
                        ))
                except (OSError, ValueError, UnicodeError, TypeError):
                    pass
                _block("Discord", seconds)
                return result(username, "unknown", "discord_rate_limited",
                              retry_after=_cooldown_remaining("Discord"))
            if error.code in (401, 403):
                _block("Discord", 180.0)
                return result(username, "unknown", "discord_access_blocked",
                              retry_after=_cooldown_remaining("Discord"))
            if error.code == 400:
                try:
                    body = json.loads(error.read(4096).decode("utf-8"))
                except (OSError, ValueError, UnicodeError):
                    body = {}
                invalid = _discord_invalid_code(body)
                if invalid:
                    checked = result(username, "invalid", "discord_" + invalid.lower())
                    _cache_set("Discord", username, checked)
                    return checked
            return result(username, "unknown", "discord_http_" + str(error.code))
        except (URLError, TimeoutError, OSError) as error:
            return result(username, "unknown", _network_reason(error, "discord"))
        except (ValueError, UnicodeError):
            return result(username, "unknown", "discord_invalid_response")

        if not isinstance(data, dict) or type(data.get("taken")) is not bool:
            return result(username, "unknown", "discord_unexpected_response")

        checked = result(
            username,
            "claimed" if data["taken"] else "available",
            "discord_reported_taken" if data["taken"] else "discord_reported_available",
            provider="Discord", cached=False,
        )
        _cache_set("Discord", username, checked)
        return checked


def test_pomelo_username(username):
    """Preserves the Owner Dashboard's separate optional Pomelo diagnostic."""
    username = str(username).strip().lower()
    valid, reason = validate_username("Discord", username)
    if not valid:
        return {**result(username, "invalid", reason), "provider": "local_validation", "source": None}
    if not _wait_slot("Pomelo", POMELO_MIN_INTERVAL):
        return {**result(username, "unknown", "pomelo_cooldown"), "provider": "Pomelo", "source": None}
    request = Request(
        POMELO_URL,
        data=json.dumps({
            "username": username, "publicLookup": True, "useCache": True,
            "includeSuggestions": False, "includeUser": False,
        }).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "winter.cc/7.3",
        },
        method="POST",
    )
    def unknown(why):
        return {**result(username, "unknown", why), "provider": "Pomelo", "source": None}
    try:
        with urlopen(request, timeout=POMELO_TIMEOUT) as response:
            data = json.load(response)
    except HTTPError as error:
        return unknown("pomelo_http_" + str(error.code))
    except (URLError, TimeoutError, OSError) as error:
        return unknown(_network_reason(error, "pomelo"))
    except (ValueError, UnicodeError):
        return unknown("pomelo_invalid_response")
    details = data.get("data") if isinstance(data, dict) else None
    if not isinstance(details, dict):
        return unknown("pomelo_unexpected_response")
    raw_status = str(details.get("status", "")).upper()
    status = {"AVAILABLE": "available", "TAKEN": "claimed", "RESERVED": "claimed"}.get(raw_status, "unknown")
    return {
        "username": username, "status": status,
        "reason": ("pomelo_reported_" + raw_status.lower()) if status != "unknown" else "pomelo_unexpected_status",
        "provider": "Pomelo", "provider_status": raw_status,
        "source": str(details.get("servedFrom", "UNKNOWN")),
        "duration": data.get("duration"),
    }


def check_username(platform, username):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")
    username = str(username).strip()
    if platform == "Discord":
        username = username.lower()
    valid, reason = validate_username(platform, username)
    if not valid:
        return result(username, "invalid", reason)
    cached = _cache_get(platform, username)
    if cached:
        return cached
    if platform == "Discord":
        return _discord(username)
    if platform == "Roblox":
        return _roblox(username)
    if platform == "Minecraft":
        return _minecraft_batch([username])[0]
    checked = _social_check(platform, username)
    _cache_set(platform, username, checked)
    return checked


def check_batch(platform, usernames):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError("Unsupported platform")
    if not isinstance(usernames, (list, tuple)):
        raise ValueError("Usernames must be a list")
    unique, seen = [], set()
    for value in usernames:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name.lower() not in seen:
            unique.append(name)
            seen.add(name.lower())
    batch = unique[:MAX_BATCH_SIZE]
    results = []
    if platform == "Minecraft":
        pending = []
        for username in batch:
            valid, why = validate_username(platform, username)
            if not valid:
                results.append(result(username, "invalid", why))
            else:
                cached = _cache_get(platform, username)
                if cached:
                    results.append(cached)
                else:
                    pending.append(username)
        resolved = {}
        for index in range(0, len(pending), MINECRAFT_BULK_SIZE):
            group = pending[index:index + MINECRAFT_BULK_SIZE]
            answer = _minecraft_batch(group)
            for item in answer:
                resolved[item["username"].lower()] = item
            if any(item["reason"] == "rate_limited" for item in answer):
                for name in pending[index + len(group):]:
                    resolved[name.lower()] = result(name, "unknown", "rate_limited")
                break
        known = {item["username"].lower(): item for item in results}
        known.update(resolved)
        results = [known[name.lower()] for name in batch]
    elif platform == "Discord":
        attempts = 0
        for i, name in enumerate(batch):
            name = name.lower()
            valid, why = validate_username(platform, name)
            if not valid:
                results.append(result(name, "invalid", why))
                continue
            cached = _cache_get(platform, name)
            if cached:
                results.append(cached)
                continue
            if attempts >= DISCORD_MAX_PER_BATCH:
                results.append(result(name, "unknown", "discord_batch_limit"))
                continue
            checked = check_username(platform, name)
            attempts += 1
            results.append(checked)
            if checked.get("reason") in ("discord_rate_limited", "discord_access_blocked"):
                for rest in batch[i + 1:]:
                    results.append(result(rest.lower(), "unknown", "discord_paused",
                                          retry_after=checked.get("retry_after", 60)))
                break
    elif platform == "Roblox":
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batch))) as pool:
            results = list(pool.map(lambda name: check_username(platform, name), batch))
    else:
        results = [check_username(platform, name) for name in batch]
    return {
        "platform": platform,
        "results": results,
        "checked": len(results),
        "total": len(unique),
        "remaining": len(unique) - len(results),
    }
