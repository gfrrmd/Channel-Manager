"""Klien HTTP tipis untuk Bot API.

Rich Messages (Bot API 10.3) dipanggil lewat sini supaya bot tidak bergantung
pada versi library tertentu: payload dikirim apa adanya sebagai JSON.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class BotAPIError(Exception):
    def __init__(self, method: str, code: int, description: str, retry_after: int | None = None):
        super().__init__(f"{method}: [{code}] {description}")
        self.method = method
        self.code = code
        self.description = description
        self.retry_after = retry_after

    @property
    def not_modified(self) -> bool:
        return "not modified" in self.description.lower()


class RawAPI:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        self._base = f"{base_url}/bot{token}/"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, /, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(4):
            try:
                resp = await self._client.post(self._base + method, json=params)
            except httpx.HTTPError as e:
                raise BotAPIError(method, 0, f"network error: {e!r}") from e
            try:
                data = resp.json()
            except ValueError:
                raise BotAPIError(method, resp.status_code, resp.text[:200])
            if data.get("ok"):
                return data["result"]
            code = int(data.get("error_code", resp.status_code))
            desc = str(data.get("description", "unknown error"))
            retry = (data.get("parameters") or {}).get("retry_after")
            if code == 429 and retry and attempt < 3:
                log.warning("Flood control di %s, tunggu %ss", method, retry)
                await asyncio.sleep(min(int(retry) + 1, 30))
                continue
            raise BotAPIError(method, code, desc, retry)
        raise BotAPIError(method, 429, "terlalu banyak percobaan")
