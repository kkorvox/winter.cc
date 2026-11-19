# winter.cc — Private Username Generator (v1.0)

Responsive web app + Windows EXE build source. Python 3.12 recommended.

## Local start (Windows)

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py admin.py create-user owner --admin
py admin.py issue-license owner
py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit http://127.0.0.1:8000 and log in using the account, password and license key. Store issued keys safely: only their hashes are stored. Keys can be revoked with `py admin.py revoke-license YOUR_KEY`. Use `py admin.py list-users` and `py admin.py list-licenses owner` for management. `--expires` accepts ISO UTC datetime when issuing a license. A license is required for both the website and desktop. Existing sessions stop working once all of a user's keys are revoked.

## Windows EXE

Run `build.bat` on Windows with Python 3.12 installed. It produces `dist\wintercc.exe`. The EXE bundles a local web server and opens a native webview window (requires Windows WebView2 runtime). It **does not** bundle the user database or issue accounts automatically. For a local-only desktop installation, provision the local database with `admin.py` on the recipient's computer first, or adapt the architecture to use a hosted authentication service. A locally administered database is not tamper-proof licensing; **for enforceable centralized revocation, a hosted authentication API must be implemented and connected to the EXE**. The web deployment uses server-side license enforcement.

## Deployment

`docker compose up --build -d` starts the private service on localhost:8000. Put a TLS reverse proxy (e.g. Caddy/Nginx) in front, enable HTTPS, set domain/DNS, configure backups and access controls, and avoid exposing port 8000 directly. Provision accounts inside the container:

```bash
docker compose exec wintercc python admin.py create-user owner --admin
docker compose exec wintercc python admin.py issue-license owner
```

For production: add CSRF/origin controls as appropriate, request throttling for login, stronger account recovery, audit logging, secret management, PostgreSQL migration for concurrent users, and TLS. `robots` noindex is not an access-control mechanism. Do not publicly deploy without security hardening.

## Availability checking

This version deliberately does **not** scrape sites or claim to have working availability APIs for Discord, Roblox, Instagram, TikTok, Telegram, or Minecraft. It provides profile links and manual statuses. `unknown` does not imply available; manually entered `available` is **user-reported**, not verified. The next search skips names marked `available` in the immediately previous search for that user and platform, then those skipped names become eligible again in the following search. Claimed names are eligible each time. A future supported API connector should use queues, backoff and provider quotas; never bypass 429 limits.

## Features

Custom amount (up to 100,000 per request), platform character filters, lengths, types, prefixes/suffixes, imported wordlist, duplicate removal, favorites, search history, presets, sorting, filtering, TXT/CSV/JSON exports, mobile-responsive dark UI, Ctrl+Enter shortcut. The first 500 results are rendered for browser performance; exports include all names.

## Known limitations

- Username constraints are approximate; platforms can change validation rules and reserve names.
- Generated names are not guaranteed to exist or be available.
- Large batches may consume memory; use smaller batches for mobile.
- Search results beyond 200 per search are not shown in history summaries (all are stored).
- Login tokens are held in sessionStorage, expire after 12 hours, and are not intended as a production-hardened auth design.
- Desktop EXE build requires a Windows machine and has not been tested in this Linux environment.
- The local EXE's license database is separate from the hosted website; centralized license enforcement is a future integration.
