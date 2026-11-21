"""winter.cc V7.11 - durable, rate-limit-aware Discord check queue.

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
        gate = conn.execute('SELECT next_at FROM discord_queue_gate WHERE id=1').fetchone()
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
