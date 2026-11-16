"""图源适配器公共接口与限速器。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .. import config


@dataclass(frozen=True)
class Candidate:
    brand_key: str
    url: str
    source: str
    source_page: str | None = None
    license: str | None = None
    attribution: str | None = None
    local_path: Path | None = None
    title: str | None = None
    width: int | None = None
    height: int | None = None


class Source(Protocol):
    name: str

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        ...


class RateLimiter:
    """按域名串行化 + 最小间隔，避免对公共服务突发请求。"""

    def __init__(self, delay: float = config.PER_HOST_DELAY) -> None:
        self.delay = delay
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc or "local"
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host, 0.0)
            gap = self.delay - (time.monotonic() - last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last[host] = time.monotonic()

    async def run(self, url: str, coro_factory, *, retries: int = config.MAX_RETRIES):
        """带限速 + 指数退避地执行一个返回 awaitable 的工厂函数。"""
        import aiohttp

        last_exc: Exception | None = None
        for attempt in range(retries):
            await self.wait(url)
            try:
                return await coro_factory()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt + 1 < retries:
                    await asyncio.sleep(config.RETRY_BACKOFF ** attempt)
        raise last_exc or RuntimeError("request failed")
