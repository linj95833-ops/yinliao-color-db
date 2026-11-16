"""品牌官网图源：遵守 robots.txt 抓取公开产品页中的包装图。

仅用于国内品牌兜底（OFF 对王老吉/元气森林/东鹏/蜜雪等几乎零收录）。
网络礼仪：
  - 抓取前读取并遵守 robots.txt（返回 HTML 的 SPA 站点视为「无规则」）
  - 可识别 UA、每域名串行 + 最小间隔、页面与 robots 结果本地缓存
  - 只抓配置中显式列出的公开页面，不做全站递归
授权见 config.OFFICIAL_LICENSE：图片版权归品牌方所有，本项目不分发二进制，
仅在 ATTRIBUTION.md 中记录来源页，非商用娱乐研究参考。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import yaml

from .. import config, db
from .base import Candidate

_IMG_SRC = re.compile(
    r"<img\b[^>]*?(?:data-original|data-src|src)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_SRCSET = re.compile(r"srcset\s*=\s*[\"']([^\"']+)[\"']", re.I)
_CSS_URL = re.compile(r"url\(\s*[\"']?([^\"')]+\.(?:jpe?g|png|webp))", re.I)
_OG_IMG = re.compile(
    r"<meta\b[^>]*?(?:property|name)=[\"']og:image[\"'][^>]*?content=[\"']([^\"']+)[\"']", re.I)
_LINKS = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_PAGE_HINT = re.compile(r"product|产品|系列|series|drink|饮品|brand", re.I)
_SKIP_ASSET = re.compile(
    r"icon|sprite|qrcode|qr_code|wechat|weibo|loading|placeholder|avatar|"
    r"favicon|logo|share|arrow|btn_|button|bg_|background|pixel|blank", re.I)


def _load_sites() -> dict[str, dict[str, Any]]:
    try:
        data = yaml.safe_load(config.OFFICIAL_SITES_FILE.read_text(encoding="utf-8")) or {}
        return {str(k): (v or {}) for k, v in data.items()}
    except (OSError, yaml.YAMLError):
        return {}


def _cache_path(prefix: str, url: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return config.CACHE_DIR / f"{prefix}_{digest}.txt"


def _read_cache(path, ttl_days: int = 7) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
        head, _, body = raw.partition("\n")
        ts = datetime.fromisoformat(head.removeprefix("#cached:"))
        if (datetime.now(timezone.utc) - ts).total_seconds() > ttl_days * 86400:
            return None
        return body
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path, text: str) -> None:
    try:
        path.write_text(f"#cached:{datetime.now(timezone.utc).isoformat()}\n{text}",
                        encoding="utf-8")
    except OSError:
        pass


class OfficialSiteSource:
    name = "official"
    _gate = asyncio.Semaphore(1)
    _last: dict[str, float] = {}

    def __init__(self) -> None:
        self.sites = _load_sites()
        self._robots: dict[str, bool | None] = {}

    async def candidates(self, brand: dict[str, Any], session, limiter) -> list[Candidate]:
        cfg = self.sites.get(brand["key"])
        if not cfg:
            return []
        pages = [str(u) for u in (cfg.get("pages") or [])]
        if not pages:
            return []
        attribution = cfg.get("attribution") or config.OFFICIAL_ATTRIBUTION

        # 从第一个页面发现 1~2 个产品页，提升产品图命中率
        extra = await self._discover(pages[0], session)
        all_pages = list(dict.fromkeys(pages + extra))[:4]

        out: list[Candidate] = []
        seen: set[str] = set()
        for page in all_pages:
            if not await self._allowed(page, session):
                db.log_fetch(brand["key"], self.name, page, False, "robots.txt disallow")
                continue
            html = await self._page(page, session)
            if not html:
                continue
            for raw in self._images(html, page):
                if raw in seen:
                    continue
                seen.add(raw)
                out.append(Candidate(
                    brand_key=brand["key"], url=raw, source=self.name,
                    source_page=page,
                    license=config.OFFICIAL_LICENSE,
                    attribution=f"{attribution} ({urlparse(page).netloc})",
                    title=urlparse(raw).path.rsplit("/", 1)[-1][:80],
                ))
                if len(out) >= config.OFFICIAL_MAX_IMAGES_PER_PAGE * len(all_pages):
                    break
        return out

    async def _throttled_get(self, url: str, session) -> str | None:
        """每域名串行 + 最小间隔；HTML 与 robots 共用。"""
        host = urlparse(url).netloc
        async with self._gate:
            gap = config.OFFICIAL_DELAY - (time.monotonic() - self._last.get(host, 0.0))
            if gap > 0:
                await asyncio.sleep(gap)
            self._last[host] = time.monotonic()
            try:
                async with session.get(url, headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                }) as resp:
                    if resp.status != 200:
                        return None
                    raw = await resp.read()
                    return raw.decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return None

    async def _robots_text(self, url: str, session) -> str:
        p = urlparse(url)
        robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
        cache = _cache_path("robots", robots_url)
        cached = _read_cache(cache, ttl_days=3)
        if cached is not None:
            return cached
        text = await self._throttled_get(robots_url, session) or ""
        _write_cache(cache, text)
        return text

    async def _allowed(self, url: str, session) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            text = await self._robots_text(url, session)
            # SPA 站点的 /robots.txt 常返回 index.html，不是 robots 规则 -> 视为无规则
            if not text or text.lstrip().lower().startswith(("<!doctype", "<html")):
                self._robots[host] = True
            else:
                rp = RobotFileParser()
                try:
                    rp.parse(text.splitlines())
                    self._robots[host] = bool(rp.can_fetch(config.USER_AGENT, url))
                except Exception:  # noqa: BLE001
                    self._robots[host] = True
        return bool(self._robots[host])

    async def _page(self, url: str, session) -> str | None:
        cache = _cache_path("page", url)
        cached = _read_cache(cache)
        if cached is not None:
            return cached
        html = await self._throttled_get(url, session)
        if html:
            _write_cache(cache, html)
        return html

    async def _discover(self, url: str, session) -> list[str]:
        """从入口页找 1~2 个同域产品页链接，不做递归。"""
        html = await self._page(url, session)
        if not html:
            return []
        base_host = urlparse(url).netloc
        out: list[str] = []
        for href, text in _LINKS.findall(html):
            label = re.sub(r"<[^>]+>", " ", text)
            if not _PAGE_HINT.search(f"{href} {label}"):
                continue
            full = urljoin(url, href.split("#")[0])
            p = urlparse(full)
            if p.scheme not in ("http", "https") or p.netloc != base_host:
                continue
            if full not in out:
                out.append(full)
            if len(out) >= 2:
                break
        return out

    @staticmethod
    def _images(html: str, page: str) -> list[str]:
        raws: list[str] = list(_IMG_SRC.findall(html))
        raws += _OG_IMG.findall(html)
        for block in _SRCSET.findall(html):
            for part in block.split(","):
                candidate = part.strip().split(" ")[0]
                if candidate:
                    raws.append(candidate)
        raws += _CSS_URL.findall(html)

        out: list[str] = []
        for raw in raws:
            if raw.startswith(("data:", "javascript:", "//")):
                if raw.startswith("//"):
                    raw = "https:" + raw
                else:
                    continue
            full = urljoin(page, raw)
            p = urlparse(full)
            if p.scheme not in ("http", "https") or not p.netloc:
                continue
            if _SKIP_ASSET.search(p.path):
                continue
            if not re.search(r"\.(jpe?g|png|webp)(\?|$)", p.path, re.I):
                continue
            if full not in out:
                out.append(full)
        return out
