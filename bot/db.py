from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    BIGINT PRIMARY KEY,
    tz         TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channels (
    id         SERIAL PRIMARY KEY,
    owner_id   BIGINT NOT NULL,
    chat_id    BIGINT NOT NULL,
    title      TEXT NOT NULL,
    username   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, chat_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id               SERIAL PRIMARY KEY,
    owner_id         BIGINT NOT NULL,
    chat_id          BIGINT NOT NULL,
    channel_title    TEXT,
    channel_username TEXT,
    content          JSONB NOT NULL,
    buttons          JSONB NOT NULL DEFAULT '[]',
    options          JSONB NOT NULL DEFAULT '{}',
    mode             TEXT NOT NULL DEFAULT 'rich',
    status           TEXT NOT NULL,
    run_at           TIMESTAMPTZ,
    message_id       BIGINT,
    sent_at          TIMESTAMPTZ,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS posts_due_idx ON posts (run_at) WHERE status = 'scheduled';
CREATE INDEX IF NOT EXISTS posts_owner_idx ON posts (owner_id, id DESC);

CREATE TABLE IF NOT EXISTS votes (
    post_id INT    NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    idx     INT    NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (post_id, idx, user_id)
);
"""

_POST_FIELDS = {
    "content", "buttons", "options", "mode", "status",
    "run_at", "message_id", "sent_at", "error",
}


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: asyncpg.Pool

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self._dsn, min_size=1, max_size=5, init=self._init_conn
        )
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def close(self) -> None:
        await self.pool.close()

    # ---------------------------------------------------------------- users
    async def get_tz(self, user_id: int, default: str) -> str:
        await self.pool.execute(
            "INSERT INTO users (user_id, tz) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id, default,
        )
        return await self.pool.fetchval("SELECT tz FROM users WHERE user_id = $1", user_id)

    async def set_tz(self, user_id: int, tz: str) -> None:
        await self.pool.execute(
            "INSERT INTO users (user_id, tz) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET tz = EXCLUDED.tz",
            user_id, tz,
        )

    # ---------------------------------------------------------------- channels
    async def add_channel(self, owner_id: int, chat_id: int, title: str, username: str | None) -> None:
        await self.pool.execute(
            "INSERT INTO channels (owner_id, chat_id, title, username) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (owner_id, chat_id) DO UPDATE SET title = EXCLUDED.title, username = EXCLUDED.username",
            owner_id, chat_id, title, username,
        )

    async def list_channels(self, owner_id: int) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT * FROM channels WHERE owner_id = $1 ORDER BY id", owner_id
        )
        return [dict(r) for r in rows]

    async def get_channel(self, owner_id: int, chat_id: int) -> dict | None:
        r = await self.pool.fetchrow(
            "SELECT * FROM channels WHERE owner_id = $1 AND chat_id = $2", owner_id, chat_id
        )
        return dict(r) if r else None

    async def remove_channel(self, owner_id: int, chat_id: int) -> None:
        await self.pool.execute(
            "DELETE FROM channels WHERE owner_id = $1 AND chat_id = $2", owner_id, chat_id
        )

    async def remove_channel_everywhere(self, chat_id: int) -> None:
        await self.pool.execute("DELETE FROM channels WHERE chat_id = $1", chat_id)

    # ---------------------------------------------------------------- posts
    async def create_post(
        self, *, owner_id: int, channel: dict, content: dict, buttons: list,
        options: dict, mode: str, status: str, run_at: datetime | None = None,
    ) -> int:
        return await self.pool.fetchval(
            "INSERT INTO posts (owner_id, chat_id, channel_title, channel_username, content, "
            "buttons, options, mode, status, run_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
            owner_id, channel["chat_id"], channel["title"], channel.get("username"),
            content, buttons, options, mode, status, run_at,
        )

    async def get_post(self, post_id: int) -> dict | None:
        r = await self.pool.fetchrow("SELECT * FROM posts WHERE id = $1", post_id)
        return dict(r) if r else None

    async def list_posts(self, owner_id: int, statuses: list[str] | None = None, limit: int = 10) -> list[dict]:
        if statuses:
            rows = await self.pool.fetch(
                "SELECT * FROM posts WHERE owner_id = $1 AND status = ANY($2::text[]) "
                "ORDER BY id DESC LIMIT $3", owner_id, statuses, limit,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM posts WHERE owner_id = $1 ORDER BY id DESC LIMIT $2", owner_id, limit
            )
        return [dict(r) for r in rows]

    async def update_post(self, post_id: int, **fields: Any) -> None:
        assert fields and set(fields) <= _POST_FIELDS
        sets = [f"{k} = ${i}" for i, k in enumerate(fields, 1)]
        await self.pool.execute(
            f"UPDATE posts SET {', '.join(sets)} WHERE id = ${len(fields) + 1}",
            *fields.values(), post_id,
        )

    async def cancel_if_scheduled(self, post_id: int) -> bool:
        r = await self.pool.fetchval(
            "UPDATE posts SET status = 'cancelled' WHERE id = $1 AND status = 'scheduled' RETURNING id", post_id
        )
        return r is not None

    async def claim_due_posts(self, limit: int = 10) -> list[dict]:
        rows = await self.pool.fetch(
            "UPDATE posts SET status = 'sending' WHERE id IN ("
            "  SELECT id FROM posts WHERE status = 'scheduled' AND run_at <= now() "
            "  ORDER BY run_at LIMIT $1 FOR UPDATE SKIP LOCKED) RETURNING *",
            limit,
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- votes
    async def toggle_vote(self, post_id: int, idx: int, user_id: int) -> bool:
        """True kalau vote ditambahkan, False kalau dibatalkan."""
        deleted = await self.pool.fetchval(
            "WITH d AS (DELETE FROM votes WHERE post_id=$1 AND idx=$2 AND user_id=$3 RETURNING 1) "
            "SELECT count(*) FROM d", post_id, idx, user_id,
        )
        if deleted:
            return False
        await self.pool.execute(
            "INSERT INTO votes (post_id, idx, user_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            post_id, idx, user_id,
        )
        return True

    async def vote_counts(self, post_id: int) -> dict[int, int]:
        rows = await self.pool.fetch(
            "SELECT idx, count(*) AS n FROM votes WHERE post_id = $1 GROUP BY idx", post_id
        )
        return {r["idx"]: r["n"] for r in rows}
