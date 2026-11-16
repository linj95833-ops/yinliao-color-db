"""兜底图源：手工 URL 清单 + robots.txt 合规检查，默认关闭。

不做自动搜刮，只下载 config/manual_urls.yaml 里逐条填写的 URL。
启用前请自行确认目标图片的授权与站点条款。
"""
from __future__ import annotations

import asyncio
import urllib.request
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .. import config, db
from ..catalog import load_manual_urls
from .base import Candidate

_ROBOTS: dict[str, RobotFileParser] = {}


def _robots_allow(url: str, ua: str, respect: bool) -> tuple[bool, str]:
    if not respect:
        return True, "robots check disabled"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "unsupported scheme"
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _ROBOTS.get(base)
    if rp is None:
        rp = RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read(200_000).decode("utf-8", "ignore")
            rp.parse(text.splitlines())
            note = "robots.txt loaded"
        except Exception as exc:  # noqa: BLE001 - 读不到 robots 时保守放行但记录
            rp.parse([])
            note = f"robots.txt unreadable ({type(exc).__name__}), allowed conservatively"
        _ROBOTS[base] = rp
    else:
        note = "robots.txt cached"
    return rp.can_fetch(ua, url), note


class ManualUrlsSource:
    name = "manual"

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        cfg = load_manual_urls()
        if not cfg["enabled"]:
            return []
        urls = (cfg["urls"] or {}).get(brand["key"]) or []
        out: list[Candidate] = []
        for raw in urls:
            url = str(raw).strip()
            if not url.startswith(("http://", "https://")):
                db.log_fetch(brand["key"], self.name, url, False, "invalid url")
                continue
            allowed, note = await asyncio.to_thread(
                _robots_allow, url, config.USER_AGENT, bool(cfg["respect_robots"])
            )
            if not allowed:
                db.log_fetch(brand["key"], self.name, url, False, f"robots disallow: {note}")
                continue
            out.append(Candidate(
                brand_key=brand["key"], url=url, source=self.name,
                license="USER_PROVIDED", attribution=f"manual url ({note})",
                title=url.rsplit("/", 1)[-1],
            ))
        return out
