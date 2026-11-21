"""winter.cc V7.12 - durable Discord queue with adaptive provider backoff.

Only confirms availability from a valid Discord answer. Never bypasses API
limits. PostgreSQL persists work across page refreshes and app restarts.
"""

import logging
import math
import threading
import time
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import checker
from .core import db, now

LOG = logging.getLogger("winter.discord_queue")
# If Discord keeps returning HTTP 429, don't make a new attempt every minute.
# These delays are a floor; the provider's own cooldown always takes priority.
REPEATED_429_DELAYS = (60, 180, 600, 1800, 3600, 7200)
_LOCK_ID = 710112681
_SCHEMA_READY = False
_SCHEMA_MUTEX = threading.Lock()
_WORKER_MUTEX = threading.Lock()
_WORKER = None
_STOP = threading.Event()


class StartQueue(BaseModel):
    search_id: int = Field(gt=0)


def _schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_MUTEX:
        if _SCHEMA_READY:
            return
        with db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discord_queue_jobs (
                    search_id BIGINT PRIMARY KEY REFERENCES searches(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    state TEXT NOT NULL DEFAULT 'active',
                    created DOUBLE PRECISION NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discord_queue_items (
                    search_id BIGINT NOT NULL REFERENCES discord_queue_jobs(search_id) ON DELETE CASCADE,
                    username TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    tries INTEGER NOT NULL DEFAULT 0,
                    due DOUBLE PRECISION NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (search_id, username)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discord_queue_gate (
                    id INTEGER PRIMARY KEY,
                    next_at DOUBLE PRECISION NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT INTO discord_queue_gate (id, next_at) VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
            """)
            # Non-destructive upgrade of the V7.11 global rate-limit state.
            conn.execute("""
                ALTER TABLE discord_queue_gate
                ADD COLUMN IF NOT EXISTS consecutive_429 INTEGER NOT NULL DEFAULT 0
            """)
            conn.execute("""
                ALTER TABLE discord_queue_gate
                ADD COLUMN IF NOT EXISTS last_reason TEXT NOT NULL DEFAULT ''
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS discord_queue_due_idx
                ON discord_queue_items (due, search_id)
                WHERE state IN ('pending', 'retry')
            """)
        _SCHEMA_READY = True


def _next_task():
    """Honor both in-process and database-persisted provider cooldowns."""
    if _cooldown_remaining() > 0:
        return None
    with db() as conn:
        gate = conn.execute('SELECT next_at, consecutive_429, last_reason FROM discord_queue_gate WHERE id=1').fetchone()
        if gate and gate['next_at'] > time.time():
            return None
        return conn.execute("""
            SELECT i.search_id, i.username, i.tries
            FROM discord_queue_items i
            JOIN discord_queue_jobs j ON j.search_id=i.search_id
            JOIN users u ON u.id=j.user_id
            JOIN results r ON r.search_id=i.search_id AND r.username=i.username
            WHERE j.state='active'
              AND i.state IN ('pending', 'retry') AND i.due<=%s
              AND r.status='unknown' AND u.suspended=0
              AND EXISTS (
                  SELECT 1 FROM licenses l WHERE l.user_id=j.user_id
                    AND l.revoked=0 AND (l.expires_at IS NULL OR l.expires_at>%s)
              )
            ORDER BY i.due ASC, j.created ASC
            LIMIT 1
        """, (time.time(), now())).fetchone()


def _cooldown_remaining():
    # Reuse the cooldown already recorded by checker.py rather than guessing.
    with checker._slots_lock:
        until = checker._cooldowns.get("Discord", 0)
    return max(0.0, until - time.monotonic())


def _store_result(task, checked):
    """Store verified results and apply a persistent, shared API backoff.

    A repeated 429 is NOT progress. The username stays queued, and the global
    interval grows rather than reissuing the same refused request each minute.
    """
    status = checked.get('status', 'unknown')
    reason = str(checked.get('reason') or 'unconfirmed')[:140]
    tries = task['tries'] + 1

    with db() as conn:
        gate = conn.execute("""
            SELECT next_at, consecutive_429
            FROM discord_queue_gate WHERE id=1 FOR UPDATE
        """).fetchone()
        failures = int(gate['consecutive_429']) if gate else 0
        provider_wait = _cooldown_remaining()

        if status in ('available', 'claimed', 'invalid'):
            state, delay = 'done', 0.0
            # Successfully confirmed results indicate the endpoint is available.
            # Don't keep a stale adaptive penalty after a successful response.
            failures = 0
            conn.execute("""
                UPDATE discord_queue_gate
                SET consecutive_429=0, last_reason='', next_at=%s
                WHERE id=1
            """, (time.time() + provider_wait,))
        elif reason in ('discord_rate_limited', 'discord_paused', 'discord_batch_limit'):
            state = 'retry'
            failures += 1
            adaptive_wait = REPEATED_429_DELAYS[min(failures - 1, len(REPEATED_429_DELAYS) - 1)]
            delay = max(provider_wait + 2.0, float(adaptive_wait))
            conn.execute("""
                UPDATE discord_queue_gate
                SET next_at=GREATEST(next_at, %s),
                    consecutive_429=%s, last_reason=%s
                WHERE id=1
            """, (time.time() + delay, failures, reason))
            LOG.warning('Discord check refused; consecutive limits=%s; next attempt in %.0fs',
                        failures, delay)
        elif reason == 'discord_access_blocked':
            # 401/403: an access restriction is NOT a temporary 60s cooldown.
            state = 'unresolved' if tries >= 3 else 'retry'
            delay = max(3600.0, provider_wait + 2.0)
            conn.execute("""
                UPDATE discord_queue_gate
                SET next_at=GREATEST(next_at, %s), last_reason=%s
                WHERE id=1
            """, (time.time() + delay, reason))
        else:
            # Transient server/network issue. Eventually leave it Unknown.
            state = 'unresolved' if tries >= 6 else 'retry'
            delay = float(min(3600, 30 * 2 ** min(tries, 7)))
            if provider_wait:
                delay = max(delay, provider_wait + 2.0)
                conn.execute("""
                    UPDATE discord_queue_gate
                    SET next_at=GREATEST(next_at, %s)
                    WHERE id=1
                """, (time.time() + provider_wait + 2.0,))

        if state == 'done':
            conn.execute("""
                UPDATE results SET status=%s
                WHERE search_id=%s AND username=%s AND status='unknown'
            """, (status, task['search_id'], task['username']))
        conn.execute("""
            UPDATE discord_queue_items SET
              state=%s, tries=%s, due=%s, reason=%s
            WHERE search_id=%s AND username=%s
              AND state IN ('pending', 'retry')
        """, (state, tries, time.time() + delay, reason,
              task['search_id'], task['username']))
        conn.execute("""
            UPDATE discord_queue_jobs SET state='finished'
            WHERE search_id=%s AND state='active'
              AND NOT EXISTS (
                SELECT 1 FROM discord_queue_items
                WHERE search_id=%s AND state IN ('pending','retry')
              )
        """, (task['search_id'], task['search_id']))

def _work_once():
    # Session-level advisory lock allows just one Discord worker at a time,
    # including if Render ever runs multiple worker processes.
    with db() as lock_conn:
        locked = lock_conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS ok", (_LOCK_ID,)
        ).fetchone()['ok']
        if not locked:
            return False
        try:
            task = _next_task()
            if not task:
                return False
            try:
                checked = checker.check_username('Discord', task['username'])
            except Exception:
                LOG.exception('Discord checker exception (no availability assumed)')
                checked = {'status': 'unknown', 'reason': 'discord_checker_error'}
            _store_result(task, checked)
            return True
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))


def _loop():
    while not _STOP.is_set():
        try:
            processed = _work_once()
            # Don't hammer the provider or the DB. A due retry won't be
            # attempted before its scheduled time.
            _STOP.wait(2 if processed else 10)
        except Exception:
            LOG.exception('Discord queue worker: will retry later')
            _STOP.wait(20)


def start_worker():
    global _WORKER
    _schema()
    with _WORKER_MUTEX:
        if _WORKER is None or not _WORKER.is_alive():
            _STOP.clear()
            _WORKER = threading.Thread(
                target=_loop, name='winter-discord-queue', daemon=True
            )
            _WORKER.start()


def _snapshot(search_id):
    with db() as conn:
        items = conn.execute("""
            SELECT i.username, i.state, i.tries, i.due, i.reason, r.status
            FROM discord_queue_items i
            JOIN results r ON r.search_id=i.search_id AND r.username=i.username
            WHERE i.search_id=%s ORDER BY i.username
        """, (search_id,)).fetchall()
        job = conn.execute(
            'SELECT state FROM discord_queue_jobs WHERE search_id=%s',
            (search_id,)
        ).fetchone()
        gate = conn.execute('SELECT next_at, consecutive_429, last_reason FROM discord_queue_gate WHERE id=1').fetchone()
    if not job:
        raise HTTPException(404, 'Queue not found')
    counts = Counter(item['state'] for item in items)
    active = [item['due'] for item in items if item['state'] in ('pending','retry')]
    next_retry = max(0, int(math.ceil(
        max(min(active), (gate or {}).get('next_at', 0)) - time.time()
    ))) if active else 0
    return {
        'search_id': search_id,
        'state': job['state'],
        'total': len(items),
        'verified': counts['done'],
        'unresolved': counts['unresolved'],
        'remaining': counts['pending']+counts['retry'],
        'retry_after': next_retry,
        # A repeated cooldown can reflect repeated API rejection, not progress.
        'provider_limited': bool((gate or {}).get('consecutive_429', 0)),
        'consecutive_429': (gate or {}).get('consecutive_429', 0),
        'last_error': (gate or {}).get('last_reason', ''),
        'results': [{
            'username': x['username'], 'status': x['status'],
            'queue_state': x['state'], 'reason': x['reason'],
            'tries': x['tries']
        } for x in items],
    }


def attach(app, require):
    router = APIRouter(prefix='/api/discord-queue', tags=['discord-queue'])

    @router.post('/start')
    def start(body: StartQueue, user=Depends(require)):
        _schema()
        with db() as conn:
            search = conn.execute("""
                SELECT id, platform FROM searches
                WHERE id=%s AND user_id=%s
            """, (body.search_id, user['id'])).fetchone()
            if not search:
                raise HTTPException(404, 'Search not found')
            if search['platform'] != 'Discord':
                raise HTTPException(400, 'This queue is for Discord searches')
            amount = conn.execute("""
                SELECT COUNT(*) AS total FROM results WHERE search_id=%s
            """, (body.search_id,)).fetchone()['total']
            if amount > 10000:
                raise HTTPException(400, 'Limit is 10,000 usernames per queued search')
            # Older searches could mark names "skipped" because they looked
            # available in an earlier run. Verify them again this time.
            conn.execute("""
                UPDATE results SET status='unknown'
                WHERE search_id=%s AND status='skipped'
            """, (body.search_id,))
            # One active job per account avoids letting abandoned searches
            # consume provider requests indefinitely.
            conn.execute("""
                UPDATE discord_queue_jobs SET state='cancelled'
                WHERE user_id=%s AND search_id<>%s AND state='active'
            """, (user['id'], body.search_id))
            conn.execute("""
                INSERT INTO discord_queue_jobs (search_id, user_id, created)
                VALUES (%s,%s,%s)
                ON CONFLICT (search_id) DO NOTHING
            """, (body.search_id, user['id'], time.time()))
            conn.execute("""
                INSERT INTO discord_queue_items (search_id, username, state)
                SELECT search_id, username,
                  CASE WHEN status='unknown' THEN 'pending' ELSE 'done' END
                FROM results WHERE search_id=%s
                ON CONFLICT (search_id, username) DO NOTHING
            """, (body.search_id,))
        start_worker()
        return _snapshot(body.search_id)

    @router.get('/{search_id}')
    def status(search_id: int, user=Depends(require)):
        _schema()
        with db() as conn:
            row = conn.execute(
                'SELECT user_id FROM discord_queue_jobs WHERE search_id=%s',
                (search_id,)
            ).fetchone()
        if not row or row['user_id'] != user['id']:
            raise HTTPException(404, 'Queue not found')
        start_worker()
        return _snapshot(search_id)

    app.include_router(router)
    app.add_event_handler('startup', start_worker)
